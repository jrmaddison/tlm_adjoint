"""This module includes functionality for interacting with Firedrake variables
and Dirichlet boundary conditions.
"""

from .backend import (
    FiniteElement, TensorElement, TestFunction, TrialFunction, VectorElement,
    backend_Constant, backend_DirichletBC, backend_ScalarType, complex_mode)
from ..interface import (
    SpaceInterface, VariableInterface, VariableStateChangeError,
    add_replacement_interface, is_var, space_comm, var_comm, var_dtype,
    var_increment_state_lock, var_is_cached, var_is_replacement, var_is_static,
    var_linf_norm, var_lock_state, var_replacement, var_scalar_value,
    var_space, var_space_type)

from ..caches import Caches
from ..manager import paused_manager

from collections.abc import Sequence
import functools
import itertools
import numbers
import numpy as np
import ufl
import weakref

__all__ = \
    [
        "Constant",

        "Zero",
        "ZeroConstant",
        "eliminate_zeros",

        "Replacement",
        "ReplacementConstant",
        "ReplacementFunction",
        "ReplacementZeroConstant",
        "ReplacementZeroFunction",

        "DirichletBC",
        "HomogeneousDirichletBC"
    ]


class ConstantSpaceInterface(SpaceInterface):
    def _comm(self):
        return self._tlm_adjoint__space_interface_attrs["comm"]

    def _dtype(self):
        return self._tlm_adjoint__space_interface_attrs["dtype"]

    def _id(self):
        return self._tlm_adjoint__space_interface_attrs["id"]

    def _new(self, *, name=None, space_type="primal", static=False,
             cache=None):
        domain = self._tlm_adjoint__space_interface_attrs["domain"]
        return Constant(name=name, domain=domain, space=self,
                        space_type=space_type, static=static, cache=cache)


class ConstantInterface(VariableInterface):
    def _space(self):
        return self._tlm_adjoint__var_interface_attrs["space"]

    def _space_type(self):
        return self._tlm_adjoint__var_interface_attrs["space_type"]

    def _dtype(self):
        return self._tlm_adjoint__var_interface_attrs["dtype"]

    def _id(self):
        return self._tlm_adjoint__var_interface_attrs["id"]

    def _name(self):
        return self._tlm_adjoint__var_interface_attrs["name"](self)

    def _state(self):
        return self._tlm_adjoint__var_interface_attrs["state"][0]

    def _update_state(self):
        self._tlm_adjoint__var_interface_attrs["state"][0] += 1

    def _is_static(self):
        return self._tlm_adjoint__var_interface_attrs["static"]

    def _is_cached(self):
        return self._tlm_adjoint__var_interface_attrs["cache"]

    def _caches(self):
        if "caches" not in self._tlm_adjoint__var_interface_attrs:
            self._tlm_adjoint__var_interface_attrs["caches"] \
                = Caches(self)
        return self._tlm_adjoint__var_interface_attrs["caches"]

    def _zero(self):
        if len(self.ufl_shape) == 0:
            value = 0.0
        else:
            value = np.zeros(self.ufl_shape, dtype=var_dtype(self))
            value = backend_Constant(value)
        self.assign(value)

    def _assign(self, y):
        if isinstance(y, numbers.Complex):
            if len(self.ufl_shape) != 0:
                raise ValueError("Invalid shape")
            self.assign(y)
        elif isinstance(y, backend_Constant):
            if y.ufl_shape != self.ufl_shape:
                raise ValueError("Invalid shape")
            self.assign(y)
        else:
            if len(self.ufl_shape) != 0:
                raise ValueError("Invalid shape")
            self.assign(var_scalar_value(y))

    def _axpy(self, alpha, x, /):
        if isinstance(x, backend_Constant):
            if x.ufl_shape != self.ufl_shape:
                raise ValueError("Invalid shape")
            if len(self.ufl_shape) == 0:
                self.assign(self + alpha * x)
            else:
                value = self.values() + alpha * x.values()
                value.shape = self.ufl_shape
                value = backend_Constant(value)
                self.assign(value)
        else:
            if len(self.ufl_shape) == 0:
                self.assign(
                    var_scalar_value(self) + alpha * var_scalar_value(x))
            else:
                raise ValueError("Invalid shape")

    def _inner(self, y):
        if isinstance(y, backend_Constant):
            if y.ufl_shape != self.ufl_shape:
                raise ValueError("Invalid shape")
            return y.values().conjugate().dot(self.values())
        else:
            raise TypeError(f"Unexpected type: {type(y)}")

    def _linf_norm(self):
        values = self.values()
        if len(values) == 0:
            return var_dtype(self)(0.0).real.dtype.type(0.0)
        else:
            return abs(values).max()

    def _local_size(self):
        comm = var_comm(self)
        if comm.rank == 0:
            if len(self.ufl_shape) == 0:
                return 1
            else:
                return np.prod(self.ufl_shape)
        else:
            return 0

    def _global_size(self):
        if len(self.ufl_shape) == 0:
            return 1
        else:
            return np.prod(self.ufl_shape)

    def _local_indices(self):
        comm = var_comm(self)
        if comm.rank == 0:
            if len(self.ufl_shape) == 0:
                return slice(0, 1)
            else:
                return slice(0, np.prod(self.ufl_shape))
        else:
            return slice(0, 0)

    def _get_values(self):
        comm = var_comm(self)
        if comm.rank == 0:
            values = self.values().copy()
        else:
            values = np.array([], dtype=var_dtype(self))
        return values

    def _set_values(self, values):
        comm = var_comm(self)
        if comm.rank != 0:
            values = None
        values = comm.bcast(values, root=0)
        if len(self.ufl_shape) == 0:
            values.shape = (1,)
            self.assign(values[0])
        else:
            values.shape = self.ufl_shape
            self.assign(backend_Constant(values))

    def _replacement(self):
        if "replacement" not in self._tlm_adjoint__var_interface_attrs:
            count = self._tlm_adjoint__var_interface_attrs["replacement_count"]
            if isinstance(self, Zero):
                self._tlm_adjoint__var_interface_attrs["replacement"] = \
                    ReplacementZeroConstant(self, count=count)
            else:
                self._tlm_adjoint__var_interface_attrs["replacement"] = \
                    ReplacementConstant(self, count=count)
        return self._tlm_adjoint__var_interface_attrs["replacement"]

    def _is_replacement(self):
        return False

    def _is_scalar(self):
        return len(self.ufl_shape) == 0

    def _scalar_value(self):
        # assert var_is_scalar(self)
        value, = self.values()
        return var_dtype(self)(value)

    def _is_alias(self):
        return "alias" in self._tlm_adjoint__var_interface_attrs


def constant_value(value=None, shape=None):
    if value is None:
        if shape is None:
            shape = ()
    elif shape is not None:
        value_ = value
        if not isinstance(value_, np.ndarray):
            value_ = np.array(value_)
        if value_.shape != shape:
            raise ValueError("Invalid shape")
        del value_

    # Default value
    if value is None:
        if len(shape) == 0:
            value = 0.0
        else:
            value = np.zeros(shape, dtype=backend_ScalarType)

    return value


class Constant(backend_Constant):
    """Extends the :class:`firedrake.constant.Constant` class.

    :arg value: The initial value. `None` indicates a value of zero.
    :arg name: A :class:`str` name.
    :arg domain: The domain on which the :class:`.Constant` is defined.
    :arg space: The space on which the :class:`.Constant` is defined.
    :arg space_type: The space type for the :class:`.Constant`. `'primal'`,
        `'dual'`, `'conjugate'`, or `'conjugate_dual'`.
    :arg shape: A :class:`tuple` of :class:`int` objects defining the shape of
        the value.
    :arg comm: The communicator for the :class:`.Constant`.
    :arg static: Defines whether the :class:`.Constant` is static, meaning that
        it is stored by reference in checkpointing/replay, and an associated
        tangent-linear variable is zero.
    :arg cache: Defines whether results involving the :class:`.Constant` may be
        cached. Default `static`.

    Remaining arguments are passed to the :class:`firedrake.constant.Constant`
    constructor.
    """

    def __init__(self, value=None, *args, name=None, domain=None, space=None,
                 space_type="primal", shape=None, comm=None, static=False,
                 cache=None, **kwargs):
        if space_type not in {"primal", "conjugate", "dual", "conjugate_dual"}:
            raise ValueError("Invalid space type")

        if domain is None and space is not None:
            domains = space.ufl_domains()
            if len(domains) > 0:
                domain, = domains
            del domains

        # Shape initialization / checking
        if space is not None:
            if shape is None:
                shape = space.ufl_element().value_shape
            elif shape != space.ufl_element().value_shape:
                raise ValueError("Invalid shape")

        value = constant_value(value, shape)

        # Default comm
        if comm is None and space is not None:
            comm = space_comm(space)

        if cache is None:
            cache = static

        with paused_manager():
            super().__init__(
                value, *args, name=name, domain=domain, space=space,
                comm=comm, **kwargs)
        self._tlm_adjoint__var_interface_attrs.d_setitem("space_type", space_type)  # noqa: E501
        self._tlm_adjoint__var_interface_attrs.d_setitem("static", static)
        self._tlm_adjoint__var_interface_attrs.d_setitem("cache", cache)

    def __new__(cls, value=None, *args, name=None, domain=None,
                space_type="primal", shape=None, static=False, cache=None,
                **kwargs):
        if domain is None:
            return object().__new__(cls)
        else:
            value = constant_value(value, shape)
            if space_type not in {"primal", "conjugate",
                                  "dual", "conjugate_dual"}:
                raise ValueError("Invalid space type")
            if cache is None:
                cache = static
            F = super().__new__(cls, value, domain=domain)
            F.rename(name=name)
            F._tlm_adjoint__var_interface_attrs.d_setitem("space_type", space_type)  # noqa: E501
            F._tlm_adjoint__var_interface_attrs.d_setitem("static", static)
            F._tlm_adjoint__var_interface_attrs.d_setitem("cache", cache)
            return F


class Zero:
    """Mixin for defining a zero-valued variable. Used for zero-valued
    variables for which UFL zero elimination should not be applied.
    """

    def _tlm_adjoint__var_interface_update_state(self):
        raise VariableStateChangeError("Cannot call _update_state interface "
                                       "of Zero")


class ZeroConstant(Constant, Zero):
    """A :class:`.Constant` which is flagged as having a value of zero.

    Arguments are passed to the :class:`.Constant` constructor, together with
    `static=True` and `cache=True`.
    """

    def __init__(self, *, name=None, domain=None, space=None,
                 space_type="primal", shape=None, comm=None):
        Constant.__init__(
            self, name=name, domain=domain, space=space, space_type=space_type,
            shape=shape, comm=comm, static=True, cache=True)
        var_lock_state(self)
        if var_linf_norm(self) != 0.0:
            raise RuntimeError("ZeroConstant is not zero-valued")

    def __new__(cls, *args, shape=None, **kwargs):
        return Constant.__new__(
            cls, constant_value(shape=shape), *args,
            shape=shape, static=True, cache=True, **kwargs)


def constant_space(shape, *, domain=None):
    if domain is None:
        cell = None
    else:
        cell = domain.ufl_cell()

    if len(shape) == 0:
        element = FiniteElement("R", cell, 0)
    elif len(shape) == 1:
        dim, = shape
        element = VectorElement("R", cell, 0, dim=dim)
    else:
        element = TensorElement("R", cell, 0, shape=shape)

    return ufl.classes.FunctionSpace(domain, element)


def iter_expr(expr, *, evaluate_weights=False):
    if isinstance(expr, ufl.classes.FormSum):
        for weight, comp in zip(expr.weights(), expr.components()):
            if evaluate_weights:
                weight = complex(weight)
                if weight.imag == 0:
                    weight = weight.real
            yield (weight, comp)
    elif isinstance(expr, (ufl.classes.Action, ufl.classes.Coargument,
                           ufl.classes.Cofunction, ufl.classes.Expr,
                           ufl.classes.Form)):
        yield (1, expr)
    elif isinstance(expr, ufl.classes.ZeroBaseForm):
        return
        yield
    else:
        raise TypeError(f"Unexpected type: {type(expr)}")


def form_cached(key):
    def wrapper(fn):
        @functools.wraps(fn)
        def wrapped(expr, *args, **kwargs):
            if isinstance(expr, ufl.classes.Form) and key in expr._cache:
                value = expr._cache[key]
            else:
                value = fn(expr, *args, **kwargs)
                if isinstance(expr, ufl.classes.Form):
                    assert key not in expr._cache
                    expr._cache[key] = value
            return value
        return wrapped
    return wrapper


@form_cached("_tlm_adjoint__form_coefficients")
def extract_coefficients(expr):
    def as_ufl(expr):
        if isinstance(expr, (ufl.classes.BaseForm,
                             ufl.classes.Expr,
                             numbers.Complex)):
            return ufl.as_ufl(expr)
        elif isinstance(expr, Sequence):
            return ufl.as_vector(tuple(map(as_ufl, expr)))
        else:
            raise TypeError(f"Unexpected type: {type(expr)}")

    deps = []
    for c in (ufl.coefficient.BaseCoefficient, backend_Constant):
        c_deps = {}
        for dep in itertools.chain.from_iterable(map(
                lambda expr: ufl.algorithms.extract_type(as_ufl(expr), c),
                itertools.chain.from_iterable(iter_expr(as_ufl(expr))))):
            c_deps[dep.count()] = dep
        deps.extend(sorted(c_deps.values(), key=lambda dep: dep.count()))
    return deps


def with_coefficient(expr, x):
    if isinstance(x, ufl.classes.Coefficient):
        return expr, {}, {}
    else:
        x_coeff = ufl.classes.Coefficient(var_space(x))
        replace_map = {x: x_coeff}
        replace_map_inverse = {x_coeff: x}
        return ufl.replace(expr, replace_map), replace_map, replace_map_inverse


def derivative(expr, x, argument=None, *,
               enable_automatic_argument=True):
    expr_arguments = ufl.algorithms.extract_arguments(expr)
    arity = len(expr_arguments)

    if argument is None and enable_automatic_argument:
        Argument = {0: TestFunction, 1: TrialFunction}[arity]
        argument = Argument(var_space(x))

    for expr_argument in expr_arguments:
        if expr_argument.number() >= arity:
            raise ValueError("Unexpected argument")
    if argument is not None:
        for expr_argument in ufl.algorithms.extract_arguments(argument):
            if expr_argument.number() < arity - int(isinstance(x, ufl.classes.Cofunction)):  # noqa: E501
                raise ValueError("Invalid argument")

    expr, replace_map, replace_map_inverse = with_coefficient(expr, x)
    x = replace_map.get(x, x)
    if argument is not None:
        argument = ufl.replace(argument, replace_map)

    if any(isinstance(comp, ufl.classes.Action)
           for _, comp in iter_expr(expr)):
        dexpr = None
        for weight, comp in iter_expr(expr):
            if isinstance(comp, ufl.classes.Action):
                if complex_mode:
                    # See Firedrake issue #3346
                    raise NotImplementedError("Complex case not implemented")

                dcomp = ufl.algorithms.expand_derivatives(
                    ufl.derivative(ufl.as_ufl(weight), x, argument=argument))
                if not isinstance(dcomp, ufl.classes.Zero):
                    raise NotImplementedError("Weight derivatives not "
                                              "implemented")

                dcomp = ufl.algorithms.expand_derivatives(
                    ufl.derivative(comp.left(), x, argument=argument))
                dcomp = weight * ufl.classes.Action(dcomp, comp.right())
                dexpr = dcomp if dexpr is None else dexpr + dcomp

                dcomp = ufl.algorithms.expand_derivatives(
                    ufl.derivative(comp.right(), x, argument=argument))
                dcomp = weight * ufl.classes.Action(comp.left(), dcomp)
                dexpr = dcomp if dexpr is None else dexpr + dcomp
            else:
                dcomp = ufl.derivative(weight * comp, x, argument=argument)
                dexpr = dcomp if dexpr is None else dexpr + dcomp
        assert dexpr is not None
    else:
        dexpr = ufl.derivative(expr, x, argument=argument)

    dexpr = ufl.algorithms.expand_derivatives(dexpr)
    return ufl.replace(dexpr, replace_map_inverse)


def expr_zero(expr):
    if isinstance(expr, ufl.classes.BaseForm):
        return ufl.classes.ZeroBaseForm(expr.arguments())
    elif isinstance(expr, ufl.classes.Expr):
        return ufl.classes.Zero(shape=expr.ufl_shape,
                                free_indices=expr.ufl_free_indices,
                                index_dimensions=expr.ufl_index_dimensions)
    else:
        raise TypeError(f"Unexpected type: {type(expr)}")


@form_cached("_tlm_adjoint__simplified_form")
def eliminate_zeros(expr):
    """Apply zero elimination for :class:`.Zero` objects in the supplied
    :class:`ufl.core.expr.Expr` or :class:`ufl.form.BaseForm`.

    :arg expr: A :class:`ufl.core.expr.Expr` or :class:`ufl.form.BaseForm`.
    :returns: A :class:`ufl.core.expr.Expr` or :class:`ufl.form.BaseForm` with
        zero elimination applied. May return `expr`.
    """

    replace_map = {c: expr_zero(c)
                   for c in extract_coefficients(expr)
                   if isinstance(c, Zero)}
    if len(replace_map) == 0:
        simplified_expr = expr
    else:
        simplified_expr = ufl.replace(expr, replace_map)

    if isinstance(simplified_expr, ufl.classes.BaseForm):
        nonempty_expr = ufl.classes.ZeroBaseForm(expr.arguments())
        for weight, comp in iter_expr(simplified_expr):
            if not isinstance(comp, ufl.classes.Form) or not comp.empty():
                nonempty_expr = nonempty_expr + weight * comp
        simplified_expr = nonempty_expr

    return simplified_expr


class DirichletBC(backend_DirichletBC):
    """Extends the :class:`firedrake.bcs.DirichletBC` class.

    :arg static: A flag that indicates that the value for the
        :class:`.DirichletBC` will not change, and which determines whether
        calculations involving this :class:`.DirichletBC` can be cached. If
        `None` then autodetected from the value.

    Remaining arguments are passed to the :class:`firedrake.bcs.DirichletBC`
    constructor.
    """

    # Based on FEniCS 2019.1.0 DirichletBC API
    def __init__(self, V, g, sub_domain, *args,
                 static=None, _homogeneous=False, **kwargs):
        super().__init__(V, g, sub_domain, *args, **kwargs)

        if static is None:
            for dep in extract_coefficients(g):
                if not is_var(dep) or not var_is_static(dep):
                    static = False
                    break
            else:
                static = True

        if static and is_var(self.function_arg):
            var_increment_state_lock(self.function_arg, self)

        self._tlm_adjoint__static = static
        self._tlm_adjoint__cache = static
        self._tlm_adjoint__homogeneous = _homogeneous


class HomogeneousDirichletBC(DirichletBC):
    """A :class:`.DirichletBC` whose value is zero.

    Arguments are passed to the :class:`.DirichletBC` constructor, together
    with `static=True`.
    """

    # Based on FEniCS 2019.1.0 DirichletBC API
    def __init__(self, V, sub_domain, *args, **kwargs):
        shape = V.ufl_element().value_shape
        if len(shape) == 0:
            g = 0.0
        else:
            g = np.zeros(shape, dtype=backend_ScalarType)
        super().__init__(V, g, sub_domain, *args, static=True,
                         _homogeneous=True, **kwargs)


def bcs_is_static(bcs):
    if isinstance(bcs, backend_DirichletBC):
        bcs = (bcs,)
    for bc in bcs:
        if not getattr(bc, "_tlm_adjoint__static", False):
            return False
    return True


def bcs_is_cached(bcs):
    if isinstance(bcs, backend_DirichletBC):
        bcs = (bcs,)
    for bc in bcs:
        if not getattr(bc, "_tlm_adjoint__cache", False):
            return False
    return True


def bcs_is_homogeneous(bcs):
    if isinstance(bcs, backend_DirichletBC):
        bcs = (bcs,)
    for bc in bcs:
        if not getattr(bc, "_tlm_adjoint__homogeneous", False):
            return False
    return True


class Replacement:
    """Represents a symbolic variable but with no value.
    """


def new_count(counted_class):
    # __slots__ workaround
    class Counted(ufl.utils.counted.Counted):
        pass

    return Counted(counted_class=counted_class).count()


class ReplacementConstant(Replacement, ufl.classes.ConstantValue,
                          ufl.utils.counted.Counted):
    """Represents a symbolic :class:`firedrake.constant.Constant`, but has no
    value.
    """

    def __init__(self, x, count):
        Replacement.__init__(self)
        ufl.classes.ConstantValue.__init__(self)
        ufl.utils.counted.Counted.__init__(
            self, count=count, counted_class=x._counted_class)
        self._tlm_adjoint__ufl_shape = tuple(x.ufl_shape)
        add_replacement_interface(self, x)

    def __repr__(self):
        return f"<{type(self)} with count {self.count()}>"

    @property
    def ufl_shape(self):
        return self._tlm_adjoint__ufl_shape


class ReplacementFunction(Replacement, ufl.classes.Coefficient):
    """Represents a symbolic :class:`firedrake.function.Function`, but has no
    value.
    """

    def __init__(self, x, count):
        Replacement.__init__(self)
        ufl.classes.Coefficient.__init__(self, var_space(x), count=count)
        add_replacement_interface(self, x)

    def __new__(cls, x, *args, **kwargs):
        return ufl.classes.Coefficient.__new__(cls, var_space(x),
                                               *args, **kwargs)


class ReplacementZeroConstant(ReplacementConstant, Zero):
    """Represents a symbolic :class:`firedrake.constant.Constant` which is
    zero, but has no value.
    """

    def __init__(self, *args, **kwargs):
        ReplacementConstant.__init__(self, *args, **kwargs)
        Zero.__init__(self)


class ReplacementZeroFunction(ReplacementFunction, Zero):
    """Represents a symbolic :class:`firedrake.function.Function` which is
    zero, but has no value.
    """

    def __init__(self, *args, **kwargs):
        ReplacementFunction.__init__(self, *args, **kwargs)
        Zero.__init__(self)


def replaced_form(form):
    replace_map = {}
    for c in extract_coefficients(form):
        if is_var(c) and not var_is_replacement(c):
            c_rep = var_replacement(c)
            if c_rep is not c:
                replace_map[c] = c_rep
    return ufl.replace(form, replace_map)


def define_var_alias(x, parent, *, key):
    if x is not parent:
        if "alias" in x._tlm_adjoint__var_interface_attrs:
            alias_parent, alias_key = x._tlm_adjoint__var_interface_attrs["alias"]  # noqa: E501
            alias_parent = alias_parent()
            if alias_parent is None or alias_parent is not parent \
                    or alias_key != key:
                raise ValueError("Invalid alias data")
        else:
            x._tlm_adjoint__var_interface_attrs["alias"] \
                = (weakref.ref(parent), key)
            x._tlm_adjoint__var_interface_attrs.d_setitem(
                "space_type", var_space_type(parent))
            x._tlm_adjoint__var_interface_attrs.d_setitem(
                "static", var_is_static(parent))
            x._tlm_adjoint__var_interface_attrs.d_setitem(
                "cache", var_is_cached(parent))
            x._tlm_adjoint__var_interface_attrs.d_setitem(
                "state", parent._tlm_adjoint__var_interface_attrs["state"])
