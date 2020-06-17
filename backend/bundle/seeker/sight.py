import functools
import inspect
import opcode
import os
import pathlib
import sys
import re
import collections
import datetime as datetime_module
import itertools
import threading
import traceback
from types import FrameType, FunctionType
from typing import Iterable, Tuple, Any, Mapping, Optional, List, Callable, Union

from .recorder import Recorder
from .variables import CommonVariable, Exploding, BaseVariable
from . import utils, pycompat

from io import StringIO

if pycompat.PY2:
    from io import open

# TODO I don't need ipython related things
ipython_filename_pattern = re.compile('^<ipython-input-([0-9]+)-.*>$')


def get_local_values(frame: FrameType,
                     watch: Iterable[BaseVariable] = (),
                     custom_repr=(),
                     max_length: int = None,
                     only_watch: bool = True) -> Mapping[str, Tuple[Any, str]]:
    result = collections.OrderedDict()
    if not only_watch:
        code = frame.f_code
        vars_order = (code.co_varnames + code.co_cellvars + code.co_freevars +
                      tuple(frame.f_locals.keys()))
        result_values = [(key, value) for key, value in frame.f_locals.items()]
        result_values.sort(key=lambda key_value: vars_order.index(key_value[0]))

        for (key, value) in result_values:
            result[key] = (value, utils.get_shortish_repr(value, custom_repr, max_length))

    for variable in watch:
        result.update((key, (value, utils.get_shortish_repr(value, custom_repr, max_length)))
                       for key, value in sorted(variable.values(frame)))

    return result


class UnavailableSource:
    def __getitem__(self, i):
        return u'SOURCE IS UNAVAILABLE'


source_and_path_cache = {}


def get_path_and_source_from_frame(frame):
    globs = frame.f_globals or {}
    module_name = globs.get('__name__')
    file_name = frame.f_code.co_filename
    cache_key = (module_name, file_name)
    try:
        return source_and_path_cache[cache_key]
    except KeyError:
        pass
    loader = globs.get('__loader__')

    source = None
    if hasattr(loader, 'get_source'):
        try:
            source = loader.get_source(module_name)
        except ImportError:
            pass
        if source is not None:
            source = source.splitlines()
    if source is None:
        ipython_filename_match = ipython_filename_pattern.match(file_name)
        if ipython_filename_match:
            entry_number = int(ipython_filename_match.group(1))
            try:
                import IPython
                ipython_shell = IPython.get_ipython()
                ((_, _, source_chunk),) = ipython_shell.history_manager. \
                    get_range(0, entry_number, entry_number + 1)
                source = source_chunk.splitlines()
            except Exception:
                pass
        else:
            try:
                with open(file_name, 'rb') as fp:
                    source = fp.read().splitlines()
            except utils.file_reading_errors:
                pass
    if not source:
        # We used to check `if source is None` but I found a rare bug where it
        # was empty, but not `None`, so now we check `if not source`.
        source = UnavailableSource()

    # If we just read the source from a file, or if the loader did not
    # apply tokenize.detect_encoding to decode the source into a
    # string, then we should do that ourselves.
    if isinstance(source[0], bytes):
        encoding = 'utf-8'
        for line in source[:2]:
            # File coding may be specified. Match pattern from PEP-263
            # (https://www.python.org/dev/peps/pep-0263/)
            match = re.search(br'coding[:=]\s*([-\w.]+)', line)
            if match:
                encoding = match.group(1).decode('ascii')
                break
        source = [pycompat.text_type(sline, encoding, 'replace') for sline in
                  source]

    result = (file_name, source)
    source_and_path_cache[cache_key] = result
    return result


def get_write_function(output, overwrite):
    is_path = isinstance(output, (pycompat.PathLike, str))
    if overwrite and not is_path:
        raise Exception('`overwrite=True` can only be used when writing '
                        'content to file.')
    if output is None:
        def write(s):
            stderr = sys.stderr
            try:
                stderr.write(s)
            except UnicodeEncodeError:
                # God damn Python 2
                # LMAO hahahahahaha
                stderr.write(utils.shitcode(s))
    elif is_path:
        return FileWriter(output, overwrite).write
    elif callable(output):
        write = output
    else:
        assert isinstance(output, utils.WritableStream)

        def write(s):
            output.write(s)
    return write


class FileWriter(object):
    def __init__(self, path, overwrite: bool = True):
        self.path = pycompat.text_type(path)
        self.overwrite = overwrite

    def write(self, s):
        with open(self.path, 'w' if self.overwrite else 'a',
                  encoding='utf-8') as output_file:
            output_file.write(s)
        self.overwrite = False


thread_global = threading.local()
DISABLED = bool(os.getenv('PYSNOOPER_DISABLED', ''))


class Tracer:

    _recorder: Recorder = None
    _log_file_name: Optional[pathlib.Path] = None
    _log_file_dir: Optional[str] = None

    def __init__(self, *watch_list, default_output: bool = True,
                 output: Union[str, Callable, utils.WritableStream, StringIO] = None,
                 watch=(), watch_explode=(), depth: int = 1, prefix: str = '', overwrite: bool = False,
                 thread_info: bool = False, custom_repr=(), max_variable_length: int = 100,
                 relative_time: bool = False, only_watch: bool = True):

        if output:
            self.log_path = output
        elif self._log_file_name and self._log_file_dir and not default_output:
            self.log_path = self._log_file_dir / self._log_file_name
        else:
            self.log_path = None
        self._write = get_write_function(self.log_path, overwrite)

        self.watch = [
                         v if isinstance(v, BaseVariable) else CommonVariable(v)
                         for v in utils.ensure_tuple(watch)
                     ] + [
                         v if isinstance(v, BaseVariable) else CommonVariable(v)
                         for v in utils.ensure_tuple(watch_list)
                     ] + [
                         v if isinstance(v, BaseVariable) else Exploding(v)
                         for v in utils.ensure_tuple(watch_explode)
                     ]
        self.frame_to_local_reprs = {}
        self.start_times = {}
        self.depth = depth
        self.prefix = prefix
        self.thread_info = thread_info
        self.thread_info_padding = 0
        assert self.depth >= 1
        self.target_codes = set()
        self.target_frames = set()
        self.thread_local = threading.local()
        if len(custom_repr) == 2 and \
                not all(isinstance(x, pycompat.collections_abc.Iterable) for x in custom_repr):
            custom_repr = (custom_repr,)
        self.custom_repr = custom_repr
        self.last_source_path = None
        self.max_variable_length = max_variable_length
        self.relative_time = relative_time
        self.only_watch = only_watch
        self.recorder = type(self).get_recorder()

    @classmethod
    def get_recorder(cls) -> Recorder:
        if not cls._recorder:
            cls.new_recorder()
        return cls._recorder

    @classmethod
    def new_recorder(cls) -> None:
        cls._recorder = Recorder()

    @classmethod
    def get_recorder_change_list(cls) -> List[dict]:
        return cls.get_recorder().changes

    @classmethod
    def set_log_file_name(cls, file_name: Optional[str]) -> None:
        cls._log_file_name = file_name

    @classmethod
    def set_log_file_dir(cls, file_dir: Optional[pathlib.Path]) -> None:
        cls._log_file_dir = file_dir

    def __call__(self, function_or_class):
        if DISABLED:
            return function_or_class

        if inspect.isclass(function_or_class):
            return self._wrap_class(function_or_class)
        else:
            return self._wrap_function(function_or_class)

    @classmethod
    def look_at(cls, func: FunctionType):
        """
        look at function's output and record it in the change list
        @param func: wrapped function
        @return: wrapper function
        """
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            # cls._recorder.add_ac_to_last_record('get value %s' % result)
            cls._recorder.add_ac_to_last_record(result)
            return result
        return wrapper

    def _wrap_class(self, cls):
        for attr_name, attr in cls.__dict__.items():
            # Coroutines are functions, but snooping them is not supported
            # at the moment
            if pycompat.iscoroutinefunction(attr):
                continue

            if inspect.isfunction(attr):
                setattr(cls, attr_name, self._wrap_function(attr))
        return cls

    def _wrap_function(self, function):
        self.target_codes.add(function.__code__)

        @functools.wraps(function)
        def simple_wrapper(*args, **kwargs):
            with self:
                return function(*args, **kwargs)

        @functools.wraps(function)
        def generator_wrapper(*args, **kwargs):
            gen = function(*args, **kwargs)
            method, incoming = gen.send, None
            while True:
                with self:
                    try:
                        outgoing = method(incoming)
                    except StopIteration:
                        return
                try:
                    method, incoming = gen.send, (yield outgoing)
                except Exception as e:
                    method, incoming = gen.throw, e

        if pycompat.iscoroutinefunction(function):
            raise NotImplementedError
        if pycompat.isasyncgenfunction(function):
            raise NotImplementedError
        elif inspect.isgeneratorfunction(function):
            return generator_wrapper
        else:
            return simple_wrapper

    def write(self, s):
        s = u'{self.prefix}{s}\n'.format(**locals())
        self._write(s)

    def __enter__(self):
        if DISABLED:
            return
        calling_frame = inspect.currentframe().f_back
        if not self._is_internal_frame(calling_frame):
            calling_frame.f_trace = self.trace
            self.target_frames.add(calling_frame)

        stack = self.thread_local.__dict__.setdefault(
            'original_trace_functions', []
        )
        stack.append(sys.gettrace())
        self.start_times[calling_frame] = datetime_module.datetime.now()
        sys.settrace(self.trace)

    def __exit__(self, exc_type, exc_value, exc_traceback):
        if DISABLED:
            return
        stack = self.thread_local.original_trace_functions
        sys.settrace(stack.pop())
        calling_frame = inspect.currentframe().f_back
        self.target_frames.discard(calling_frame)
        self.frame_to_local_reprs.pop(calling_frame, None)

        ### Writing elapsed time: #############################################
        #                                                                     #
        start_time = self.start_times.pop(calling_frame)
        duration = datetime_module.datetime.now() - start_time
        elapsed_time_string = pycompat.timedelta_format(duration)
        indent = ' ' * 4 * (thread_global.depth + 1)
        self.write(
            '{indent}Elapsed time: {elapsed_time_string}'.format(**locals())
        )
        #                                                                     #
        ### Finished writing elapsed time. ####################################

    def _is_internal_frame(self, frame):
        return frame.f_code.co_filename == Tracer.__enter__.__code__.co_filename

    def set_thread_info_padding(self, thread_info):
        current_thread_len = len(thread_info)
        self.thread_info_padding = max(self.thread_info_padding,
                                       current_thread_len)
        return thread_info.ljust(self.thread_info_padding)

    def trace(self, frame, event, arg):

        ### Checking whether we should trace this line: #######################
        #                                                                     #
        # We should trace this line either if it's in the decorated function,
        # or the user asked to go a few levels deeper and we're within that
        # number of levels deeper.

        if not (frame.f_code in self.target_codes or frame in self.target_frames):
            if self.depth == 1:
                # We did the most common and quickest check above, because the
                # trace function runs so incredibly often, therefore it's
                # crucial to hyper-optimize it for the common case.
                return None
            elif self._is_internal_frame(frame):
                return None
            else:
                _frame_candidate = frame
                for i in range(1, self.depth):
                    _frame_candidate = _frame_candidate.f_back
                    if _frame_candidate is None:
                        return None
                    elif _frame_candidate.f_code in self.target_codes or _frame_candidate in self.target_frames:
                        break
                else:
                    return None

        thread_global.__dict__.setdefault('depth', -1)
        if event == 'call':
            thread_global.depth += 1
        indent = ' ' * 4 * thread_global.depth

        #                                                                     #
        ### Finished checking whether we should trace this line. ##############

        # simplified time stamp
        timestamp = ' ' * 16

        # get line_no
        line_no = frame.f_lineno
        source_path, source = get_path_and_source_from_frame(frame)
        if self.last_source_path != source_path:
            self.write(u'{indent}Source path:... {source_path}'.
                       format(**locals()))
            self.last_source_path = source_path
        source_line = source[line_no - 1]
        thread_info = ""
        if self.thread_info:
            current_thread = threading.current_thread()
            thread_info = "{ident}-{name} ".format(
                ident=current_thread.ident, name=current_thread.getName())
        thread_info = self.set_thread_info_padding(thread_info)

        ### Dealing with misplaced function definition: #######################
        #                                                                     #
        if event == 'call' and source_line.lstrip().startswith('@'):
            # If a function decorator is found, skip lines until an actual
            # function definition is found.
            for candidate_line_no in itertools.count(line_no):
                try:
                    candidate_source_line = source[candidate_line_no - 1]
                except IndexError:
                    # End of source file reached without finding a function
                    # definition. Fall back to original source line.
                    break

                if candidate_source_line.lstrip().startswith('def'):
                    # Found the def line!
                    line_no = candidate_line_no
                    source_line = candidate_source_line
                    break
        #                                                                     #
        ### Finished dealing with misplaced function definition. ##############

        if event != 'return':
            self.recorder.add_record(line_no)

        ### Reporting newish and modified variables: ##########################
        #                                                                     #

        old_local_reprs = self.frame_to_local_reprs.get(frame, {})

        # TODO do I need the extra repr? Why not just use variable
        self.frame_to_local_reprs[frame] = local_reprs = get_local_values(frame,
                                                                          watch=self.watch,
                                                                          custom_repr=self.custom_repr,
                                                                          only_watch=self.only_watch)

        newish_string = ('Starting var:.. ' if event == 'call' else
                         'New var:....... ')

        for name, (value, value_repr) in local_reprs.items():

            if name not in old_local_reprs:
                if event == 'call':
                    # TODO it seems to work but I am not sure about this
                    self.recorder.add_vc_to_last_record((name, value))
                else:
                    self.recorder.add_vc_to_previous_record((name, value))
                self.write('{indent}{newish_string}{name} = {value_repr}'.format(
                    **locals()))
            elif old_local_reprs[name][1] != value_repr:
                self.recorder.add_vc_to_previous_record((name, value))
                self.write('{indent}Modified var:.. {name} = {value_repr}'.format(
                    **locals()))

        #                                                                     #
        ### Finished newish and modified variables. ###########################

        # If a call ends due to an exception, we still get a 'return' event
        # with arg = None. This seems to be the only way to tell the difference
        # https://stackoverflow.com/a/12800909/2482744
        code_byte = frame.f_code.co_code[frame.f_lasti]
        if not isinstance(code_byte, int):
            code_byte = ord(code_byte)
        ended_by_exception = (
                event == 'return'
                and arg is None
                and (opcode.opname[code_byte]
                     not in ('RETURN_VALUE', 'YIELD_VALUE'))
        )

        if ended_by_exception:
            self.write('{indent}Call ended by exception'.
                       format(**locals()))
        else:
            self.write(u'{indent}{timestamp}{thread_info}{event:9} '
                       u'{line_no:4} {source_line}'.format(**locals()))

        if event == 'return':
            self.frame_to_local_reprs.pop(frame, None)
            self.start_times.pop(frame, None)
            thread_global.depth -= 1

            if not ended_by_exception:
                return_value_repr = utils.get_shortish_repr(arg,
                                                            custom_repr=self.custom_repr,
                                                            max_length=self.max_variable_length,
                                                            )
                self.write('{indent}Return value:.. {return_value_repr}'.
                           format(**locals()))

        if event == 'exception':
            exception = '\n'.join(traceback.format_exception_only(*arg[:2])).strip()
            if self.max_variable_length:
                exception = utils.truncate(exception, self.max_variable_length)
            self.write('{indent}Exception:..... {exception}'.
                       format(**locals()))

        return self.trace