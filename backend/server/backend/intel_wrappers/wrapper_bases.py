from abc import abstractmethod, ABC

from typing import Optional, Iterable, Mapping, Callable, Any, Type, Union

from django.db import models, IntegrityError


class IntelWrapperBase(ABC):
    def __init__(self, validators: Mapping[str, Callable]):
        self.validators = validators

    def validate(self):
        for field_name, validator in self.validators.items():
            # TODO change error class
            field = getattr(self, field_name, None)
            if field is None:
                raise AssertionError('Cannot find the field {} during validation'
                                     .format(field_name))
            try:
                validator(field_name)
            except AssertionError as e:
                e.args = 'The field {} does not pass the validator and has following error: {}' \
                             .format(field_name, e),
                raise


class ModelWrapperBase(ABC):
    model_class: Optional[Type[models.Model]] = None

    def __init__(self):
        self.model: Optional[models.Model] = None

    def model_exists(self) -> bool:
        return self.model and isinstance(self.model, models.Model)

    @classmethod
    def set_model_class(cls, model_class: Type[models.Model]) -> None:
        cls.model_class = model_class

    def load_model(self, loaded_model: models.Model) -> 'ModelWrapperBase':
        self.model = loaded_model
        return self
        # TODO load override and load all the info to fields

    @abstractmethod
    def retrieve_model(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def make_new_model(self) -> None:
        raise NotImplementedError

    def get_model(self, overwrite: bool = False) -> None:
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


class SettableBase(ABC):
    def set_variables(self, **kwargs) -> 'SettableBase':
        return self

    @staticmethod
    def get_var_from_kwargs(kwargs: Mapping, var_name: str) -> Optional[Any]:
        return kwargs.get(var_name, None)


class AbstractWrapper(IntelWrapperBase, ModelWrapperBase, SettableBase, ABC):
    def __init__(self, validators: Mapping[str, Callable]):
        IntelWrapperBase.__init__(self, validators)
        ModelWrapperBase.__init__(self)
        SettableBase.__init__(self)

        self.field_names = [*self.validators.keys()]

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

    def get_model(self, overwrite: bool = False) -> None:
        try:
            self.validate()
            super().get_model()
        except AssertionError as e:
            e.args = 'Something went wrong when validating variables {} for the model {}. Error: {}' \
                         .format(list(self.validators.keys()), self.model_class, e),
            # TODO
            raise

        try:
            self.retrieve_model()
            if overwrite:
                self.overwrite_model()
        except self.model_class.DoesNotExist:
            self.make_new_model()
        except self.model_class.MultipleObjectsReturned as e:
            # which should never happen
            raise AssertionError('Multiple model instances received with model {} and variables {}. Error: {}'
                                 .format(self.model_class, list(self.validators.keys()), e))


class PublishedWrapper(AbstractWrapper, ABC):
    def __init__(self, validators: Mapping[str, Callable]):
        super(PublishedWrapper, self).__init__(validators)

    def set_variables(self, **kwargs) -> 'PublishedWrapper':
        is_published = kwargs.pop('is_published', None)
        if is_published is not None:
            self.set_published(is_published)
        super().set_variables(**kwargs)
        return self

    def set_published(self, flag: bool = True):
        if self.model_exists():
            self.model.is_published = flag
            self.save_model()