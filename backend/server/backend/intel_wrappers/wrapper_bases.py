from abc import abstractmethod, ABC

from typing import Optional, Iterable, Mapping, Callable, Any, Type, Union, MutableMapping, TypeVar, Generic

from django.core.exceptions import ValidationError
from django.db import models, IntegrityError

from backend.intel_wrappers.validators import is_published_validator, dummy_validator
from backend.model.mixins import PublishedMixin


class IntelWrapperBase(ABC):
    def __init__(self, validators: Mapping[str, Callable]):
        self.validators = validators

    def validate(self):
        for field_name, validator in self.validators.items():
            # TODO change error class
            field = getattr(self, field_name, None)
            if field is None:
                raise AssertionError('Cannot find the field `%s` during validation' % field_name)
            try:
                validator(field)
            except AssertionError as e:
                e.args = f'The field {field_name} does not pass the validator and has following error: {e}',
                raise


T = TypeVar('T')


class ModelWrapperBase(Generic[T], ABC):
    model_class: Optional[Type[T]] = None

    def __init__(self):
        self.model: Optional[T] = None

    def model_exists(self) -> bool:
        return self.model and isinstance(self.model, models.Model)

    @classmethod
    def set_model_class(cls, model_class: Type[T]) -> None:
        cls.model_class = model_class

    def load_model_var(self, loaded_model: T) -> None:
        pass

    def load_model(self, loaded_model: T, load_var: bool = True) -> 'ModelWrapperBase':
        self.model = loaded_model

        if load_var:
            self.load_model_var(loaded_model)

        return self

    @abstractmethod
    def retrieve_model(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def make_new_model(self) -> None:
        raise NotImplementedError

    def get_model(self, overwrite: bool = False, validate: bool = True) -> None:
        if not self.model_class:
            raise AssertionError
        # TODO use other error
        # TODO use get_or_create (write it manually)

    def overwrite_model(self) -> None:
        raise NotImplementedError

    def prepare_model(self) -> None:
        pass

    def finalize_model(self) -> None:
        self.save_model()

    def save_model(self) -> None:
        if self.model_exists():
            try:
                self.model.save()
            except IntegrityError as e:
                raise AssertionError('A exception occurs during saving the model {}. Error: {}'
                                     .format(self.model, e))
        else:
            raise AssertionError('Cannot save %s since model does not exist.' % self)

    def delete_model(self) -> T:
        if self.model_exists():
            return self.model.delete()
        else:
            raise AssertionError('Cannot delete %s since model does not exist.' % self)


class SettableBase(ABC):
    def set_variables(self, **kwargs) -> 'SettableBase':
        return self

    @staticmethod
    def get_var_from_kwargs(kwargs: Mapping, var_name: str) -> Optional[Any]:
        return kwargs.get(var_name, None)


class AbstractWrapper(IntelWrapperBase, ModelWrapperBase, SettableBase, ABC):
    def __init__(self, validators: MutableMapping[str, Callable]):
        self.id: Optional[str] = None
        validators['id'] = dummy_validator

        IntelWrapperBase.__init__(self, validators)
        ModelWrapperBase.__init__(self)
        SettableBase.__init__(self)

        self.field_names = [*self.validators.keys()]

    def load_model_var(self, loaded_model: T) -> None:
        super().load_model_var(loaded_model)
        self.id = loaded_model.id

    def set_variables(self, **kwargs) -> 'AbstractWrapper':
        for key, value in kwargs.items():
            if key in self.field_names:
                setattr(self, key, value)
            else:
                raise ValueError(f'The field name {key} is not in specified fields {self.field_names}')
                # TODO

        return self

    def overwrite_model(self) -> None:
        if not self.model_exists():
            return

        for field in self.validators.keys():
            # directly setting values will cause problems since in many to many / many to one fieds
            # I can only use model.add/set methods to overwrite values
            # When the field is a many to many field or a generated many to many field or a many to one field
            # the field type in Django ORM is a subclass class of Manager, generated by
            # create_forward_many_to_many_manager(), create_reverse_many_to_one_manager()
            # in related_descscriptors.py
            field_value: Union[Iterable[ModelWrapperBase], ModelWrapperBase, Any] = getattr(self, field)
            models_field = getattr(self.model, field)

            if isinstance(models_field, models.Manager):
                if not (isinstance(field_value, Iterable) and
                        all(isinstance(field_wrapper, ModelWrapperBase) for field_wrapper in field_value)):
                    raise ValueError('Many-to-many/many-to-one field has to use iterable wrapper collections')
                models_field.set(model_wrapper.model for model_wrapper in field_value)
            else:
                if isinstance(field_value, ModelWrapperBase):
                    setattr(self.model, field, field_value.model)
                else:
                    setattr(self.model, field, field_value)

    def get_model(self, overwrite: bool = False, validate: bool = True) -> None:
        if validate:
            try:
                self.validate()
                super().get_model()
            except AssertionError as e:
                e.args = 'Something went wrong when validating variables {} for the model {}. Error: {}' \
                             .format(list(self.validators.keys()), self.model_class, e),
                raise

        try:
            self.retrieve_model()
            if overwrite:
                if validate:
                    self.overwrite_model()
                else:
                    raise AssertionError('Cannot overwrite model without validations!')
        except (self.model_class.DoesNotExist, ValidationError):
            if validate:
                self.make_new_model()
            else:
                raise AssertionError('Cannot make new model without validations!')
        except self.model_class.MultipleObjectsReturned as e:
            # which should never happen
            raise AssertionError('Multiple model instances received with model {} and variables {}. Error: {}'
                                 .format(self.model_class, list(self.validators.keys()), e))


class PublishedWrapper(AbstractWrapper, ABC):
    def __init__(self, validators: MutableMapping[str, Callable]):
        self.is_published: bool = False
        validators['is_published'] = is_published_validator
        super(PublishedWrapper, self).__init__(validators)

    def load_model_var(self, loaded_model: PublishedMixin) -> None:
        super().load_model_var(loaded_model)
        self.is_published = loaded_model.is_published


S = TypeVar('S')


class VariedContentWrapper(PublishedWrapper, Generic[S], ABC):
    def __init__(self, validators: MutableMapping[str, Callable]):
        super(VariedContentWrapper, self).__init__(validators)

        self.model_class: S = self.model_class
