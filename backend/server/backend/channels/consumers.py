import json
from typing import Mapping, Optional
from enum import Enum

from channels.generic.websocket import JsonWebsocketConsumer

from backend.channels.Errors import InvalidRequestContent, UnknownRequestType
from backend.model.TutorialRelatedModel import Graph
from .helpers import generate_respond_message, generate_respond_error_message, ResponseType, \
    generate_status_response_mapping

from .utils import process_handler

PROCESSING_QUEUE_NAME = 'processing_queue'


class RequestHandlerTypes(Enum):
    ENQUEUE = 'enqueue'


class RequestConsumer(JsonWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super(RequestConsumer, self).__init__(*args, **kwargs)
        self.is_closed = True
        self.request_data: Optional[Mapping] = None

    def connect(self):
        self.is_closed = False
        super(RequestConsumer, self).connect()
    
    def close(self, code=None):
        self.is_closed = True
        super(RequestConsumer, self).close(code)

    def get_code(self) -> Optional[str]:
        if self.is_closed:
            return None
        return self.request_data.get('code')

    def get_graph_json_obj(self) -> Optional[Mapping]:
        if self.is_closed:
            return None
        try:
            return Graph.objects.get(id=self.request_data.get('graph_id')).cyjs
        except Graph.DoesNotExist:
            return None

    def before_parsing_request(self, content: Mapping, **kwargs):
        pass

    def parse_json_request(self, content: Mapping, **kwargs):
        instruction: str = content.get('instruction', None)
        if instruction is None:
            raise InvalidRequestContent('No instruction provided.')
        try:
            RequestHandlerTypes(instruction)
        except ValueError:
            raise UnknownRequestType('Invalid instruction')

        content_handler = getattr(self, instruction, None)
        content_handler(content)

    def after_parsing_request(self, content: Mapping, **kwargs):
        pass

    def receive_json(self, content: Mapping, **kwargs):
        self.before_parsing_request(content, **kwargs)

        try:
            self.parse_json_request(content, **kwargs)
        except Exception as e:
            self.send_json(generate_respond_error_message(e))

        self.after_parsing_request(content, **kwargs)

    def enqueue(self, content: Mapping, **kwargs) -> None:
        self.request_data = content['data']
        self.send_json(generate_respond_message(
            response_type=ResponseType.WAITING.value,
            response_mapping=generate_status_response_mapping('You request is queued. Please wait!')
        ))
        process_handler.enqueue(self)

    def executing(self) -> None:
        self.send_json(generate_respond_message(
            response_type=ResponseType.EXECUTING.value,
            response_mapping=generate_status_response_mapping('You code is being executed. Please wait!')
        ))

    def executed(self, response_mapping: Mapping) -> None:
        self.send_json(generate_respond_message(
            response_type=ResponseType.EXECUTED.value,
            response_mapping=response_mapping
        ))