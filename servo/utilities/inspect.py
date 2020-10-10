import collections
import dataclasses
import inspect
import typing
import warnings
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, TypeVar, Union, cast

import servo.utilities.strings


def get_instance_methods(
    obj, *, stop_at_parent: Optional[Type[Any]] = None
) -> Dict[str, Callable]:
    """
    Returns a mapping of method names to method callables in definition order, optionally traversing
    the inheritance hierarchy in method dispatch order.

    Note that the semantics of the values in the dictionary returned are dependent on the input object.
    When `obj` is an object instance, the values are bound method objects (as returned by `get_methods`).
    When `obj` is a class, the values are unbound function objects. Depending on what you are trying to
    do, this may have interesting ramifications (for example, the method signature of the callable will
    include `self` in the parameters list). This behavior is a side-effect of the lookup implementation
    which is utilized because it retains method definition order. To obtain a bound method object reference,
    go through `get_methods` or call `getattr` on an instance.

    Args:
        obj: The object or class to retrieve the instance methods for.
        stop_at_parent: The parent class to halt the inheritance traversal at. When None, only
            instance methods of `obj` are returned.

    Returns:
        A dictionary of methods in definition order.
    """
    cls = obj if inspect.isclass(obj) else obj.__class__
    methods = collections.ChainMap()
    stopped = False

    # search for instance specific methods before traversing the class hierarchy
    if not inspect.isclass(obj):
        methods.maps.append(
            dict(filter(lambda item: inspect.ismethod(item[1]), obj.__dict__.items()))
        )

    for c in inspect.getmro(cls):
        methods.maps.append(
            dict(filter(lambda item: inspect.isfunction(item[1]), c.__dict__.items()))
        )
        if not stop_at_parent or c == stop_at_parent:
            stopped = True
            break

    if not stopped:
        raise TypeError(
            f'invalid parent type "{stop_at_parent}": not found in inheritance hierarchy'
        )

    if isinstance(obj, cls):
        # Update the values to bound method references
        return dict(map(lambda name: (name, getattr(obj, name)), methods.keys()))
    else:
        return cast(dict, methods)


def get_methods(cls: Type[Any]) -> List[Tuple[str, Any]]:
    """
    Return a list of tuple of methods for the given class in alphabetical order.

    Args:
        cls: The class to retrieve the methods of.

    Returns:
        A list of tuples containing method names and bound method objects.
    """
    # retrieving the members can emit deprecation warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return inspect.getmembers(cls, inspect.ismethod)


def get_defining_class(method: Callable) -> Optional[Type[Any]]:
    """
    Return the class that defined the given method.

    Args:
        method: The method to return the defining class of.

    Return:
        The class that defined the method or None if not determined.
    """
    for cls in inspect.getmro(method.__self__.__class__):
        if method.__name__ in cls.__dict__:
            return cls

    meth = getattr(method, "__func__", method)  # fallback to __qualname__ parsing
    cls = getattr(
        inspect.getmodule(method),
        method.__qualname__.split(".<locals>", 1)[0].rsplit(".", 1)[0],
        None,
    )
    if isinstance(cls, type):
        return cls

    return None


def resolve_type_annotations(
    *annotations: List[Union[TypeVar, str]],
    globalns: Optional[Dict[str, Any]] = None,
    localns: Optional[Dict[str, Any]] = None,
) -> List[Type]:
    """Resolves a sequence of type annotations and returns the canonical types.

    Args:
        annotations: A variadic sequence of type annotations in object or string form to be resolved.
        globalns: Namespace of global symbols for resolving types. Defaults to None.
        localns Namespace of local symbols for resolving types. Defaults to None.

    Returns:
        A list of resolved types.
    """
    resolved = []
    for annotation in annotations:
        if isinstance(annotation, str):
            type_ = typing._eval_type(typing.ForwardRef(annotation), globalns, localns)
        else:
            if isinstance(annotation, (type, typing.TypeVar, typing._GenericAlias)):
                type_ = annotation
            else:
                type_ = annotation.__class__

        resolved.append(type_)

    return resolved


@dataclasses.dataclass
class CallableDescriptor:
    signature: inspect.signature
    module: Optional[str] = None
    globalns: Optional[Dict[str, Any]] = None
    localns: Optional[Dict[str, Any]] = None


def assert_equal_callable_descriptors(
    *descriptors: Tuple[CallableDescriptor, ...],
    name: Optional[str] = None,
    method: bool = False,
) -> None:
    """Validates that the given collection of callable descriptors have equivalent type signatures."""
    if not descriptors:
        raise ValueError("cannot validate an empty list of callable descriptors")

    if len(descriptors) == 1:
        return

    name = name if name is not None else str(descriptors[0].signature)
    reference_descriptor = descriptors[0]

    # Build the reference params
    reference_parameters: typing.Mapping[
        str, inspect.Parameter
    ] = reference_descriptor.signature.parameters
    reference_positional_parameters = list(
        filter(
            lambda param: param.kind
            in [inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.VAR_POSITIONAL],
            reference_parameters.values(),
        )
    )
    reference_keyword_parameters = dict(
        filter(
            lambda item: item[1].kind
            in [
                inspect.Parameter.KEYWORD_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.VAR_KEYWORD,
            ],
            reference_parameters.items(),
        )
    )
    reference_positional_only = list(
        filter(
            lambda param: param.kind == inspect.Parameter.POSITIONAL_ONLY,
            reference_positional_parameters,
        )
    )
    reference_keyword_nonvar = dict(
        filter(
            lambda item: item[1].kind != inspect.Parameter.VAR_KEYWORD,
            reference_keyword_parameters.items(),
        )
    )
    (reference_return_type,) = resolve_type_annotations(
        reference_descriptor.signature.return_annotation,
        globalns=reference_descriptor.globalns,
        localns=reference_descriptor.localns,
    )

    # Compare each descriptor and raise on mismatch
    for descriptor in descriptors[1:]:
        descriptor_parameters: typing.Mapping[
            str, inspect.Parameter
        ] = descriptor.signature.parameters
        descriptor_positional_parameters = list(
            filter(
                lambda param: param.kind
                in [
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.VAR_POSITIONAL,
                ],
                descriptor_parameters.values(),
            )
        )
        descriptor_keyword_parameters = dict(
            filter(
                lambda item: item[1].kind
                in [
                    inspect.Parameter.KEYWORD_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.VAR_KEYWORD,
                ],
                descriptor_parameters.items(),
            )
        )

        # We assume instance methods
        if method:
            args = list(descriptor_parameters.keys())
            first_arg = args.pop(0) if args else None
            if first_arg != "self":
                raise TypeError(
                    f"Invalid signature for '{name}' event handler: {descriptor.signature}, \"self\" must be the first argument"
                )

        if (
            descriptor.signature.return_annotation
            != reference_descriptor.signature.return_annotation
        ):
            (descriptor_return_type,) = resolve_type_annotations(
                descriptor.signature.return_annotation,
                globalns=descriptor.globalns,
                localns=descriptor.localns,
            )

            assert_equal_types(reference_return_type, descriptor_return_type)

        # Check for extraneous positional parameters on the handler
        descriptor_positional_only = list(
            filter(
                lambda param: param.kind == inspect.Parameter.POSITIONAL_ONLY,
                descriptor_positional_parameters,
            )
        )

        if len(descriptor_positional_only) > len(reference_positional_only):
            extra_param_names = sorted(
                list(
                    set(map(lambda p: p.name, descriptor_positional_only))
                    - set(map(lambda p: p.name, reference_positional_only))
                )
            )
            raise TypeError(
                f"Invalid type annotation for '{name}' event handler: encountered extra positional parameters ({servo.utilities.strings.join_to_series(extra_param_names)})"
            )

        # Check for extraneous keyword parameters on the handler
        descriptor_keyword_nonvar = dict(
            filter(
                lambda item: item[1].kind != inspect.Parameter.VAR_KEYWORD,
                descriptor_keyword_parameters.items(),
            )
        )

        extraneous_keywords = sorted(
            list(
                set(descriptor_keyword_nonvar.keys())
                - set(reference_keyword_nonvar.keys())
            )
        )
        if extraneous_keywords:
            raise TypeError(
                f"Invalid type annotation for '{name}' event handler: encountered extra parameters ({servo.utilities.strings.join_to_series(extraneous_keywords)})"
            )

        # Iterate the event signature parameters and see if the handler's signature satisfies each one
        for index, (parameter_name, reference_parameter) in enumerate(
            reference_parameters.items()
        ):
            reference_parameter_type = resolve_type_annotations(
                reference_parameter.annotation,
                globalns=reference_descriptor.globalns,
                localns=reference_descriptor.localns,
            )

            if reference_parameter.kind == inspect.Parameter.POSITIONAL_ONLY:
                if index > len(descriptor_positional_parameters) - 1:
                    if (
                        descriptor_positional_parameters[-1].kind
                        != inspect.Parameter.VAR_POSITIONAL
                    ):
                        raise TypeError(
                            f"Missing required positional parameter: '{parameter_name}'"
                        )

                descriptor_parameter = descriptor_positional_parameters[index]
                if descriptor_parameter != inspect.Parameter.VAR_POSITIONAL:
                    if (
                        descriptor_parameter.annotation
                        != reference_parameter.annotation
                    ):
                        (descriptor_parameter_type,) = resolve_type_annotations(
                            descriptor_parameter.annotation,
                            globalns=descriptor.globalns,
                            localns=descriptor.localns,
                        )

                        assert_equal_types(
                            reference_parameter_type, descriptor_parameter_type
                        )

            elif reference_parameter.kind == inspect.Parameter.VAR_POSITIONAL:
                # NOTE: This should never happen
                raise TypeError(
                    "Invalid signature: events cannot declare variable positional arguments (e.g. *args)"
                )

            elif reference_parameter.kind in [
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ]:
                if descriptor_parameter := descriptor_keyword_parameters.get(
                    parameter_name, None
                ):
                    # We have the keyword arg, check the types
                    if (
                        descriptor_parameter.annotation
                        != reference_parameter.annotation
                    ):
                        (descriptor_parameter_type,) = resolve_type_annotations(
                            descriptor_parameter.annotation,
                            globalns=descriptor.globalns,
                            localns=descriptor.localns,
                        )

                        assert_equal_types(
                            reference_parameter_type, descriptor_parameter_type
                        )
                else:
                    # Check if the last parameter is a VAR_KEYWORD
                    if (
                        list(descriptor_keyword_parameters.values())[-1].kind
                        != inspect.Parameter.VAR_KEYWORD
                    ):
                        raise TypeError(
                            f"Missing required parameter: '{parameter_name}': expected signature: {reference_descriptor.signature}"
                        )

            elif reference_parameter.kind == inspect.Parameter.VAR_KEYWORD:
                pass

            else:
                assert (
                    reference_parameter.kind == inspect.Parameter.VAR_KEYWORD
                ), reference_parameter.kind


def assert_equal_types(*types_: List[Type]) -> None:
    """Verifies that all of the given types are equivalent or raises a TypeError.

    Raises:
        ValueError: Raised if the types list is empty.
        TypeError: Raised if a type annotation disagreement is detected.
    """
    if not types_:
        raise ValueError(f"cannot compare an empty set of types")

    type_ = types_[0]
    for comparable_type in types_[1:]:
        if comparable_type == type_:
            continue

        type_origin = typing.get_origin(type_) or type_
        comparable_origin = typing.get_origin(comparable_type) or comparable_type

        if type_origin == comparable_origin:
            break

        # compare args
        type_args = typing.get_args(type_)
        comparable_args = typing.get_args(comparable_type)

        if type_args == comparable_args:
            continue

        for type_arg, comp_arg in zip(type_args, comparable_args):
            if type_arg == comp_arg or typing.Any in {type_arg, comp_arg}:
                continue

            raise TypeError(
                f"Incompatible type annotations: expected {repr(type_)}, but found {repr(type_arg)}"
            )
