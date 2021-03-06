#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# For tlm_adjoint copyright information see ACKNOWLEDGEMENTS in the tlm_adjoint
# root directory

# This file is part of tlm_adjoint.
#
# tlm_adjoint is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, version 3 of the License.
#
# tlm_adjoint is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with tlm_adjoint.  If not, see <https://www.gnu.org/licenses/>.

from .backend import backend
from ..interface import InterfaceException, SpaceInterface, add_interface, \
    add_new_real_function, new_function_id, new_space_id, space_id, space_new
from ..interface import FunctionInterface as _FunctionInterface
from ..tlm_adjoint import _default_comm

import copy
import numpy as np
import warnings

__all__ = \
    [
        "Function",
        "FunctionSpace",
        "Replacement",

        "RealFunctionSpace",
        "copy_parameters_dict",
        "default_comm",
        "function_space_id",
        "function_space_new",
        "info",
        "warning"
    ]


class FunctionSpaceInterface(SpaceInterface):
    def _comm(self):
        return self.comm()

    def _id(self):
        return self.id()

    def _new(self, name=None, static=False, cache=None, checkpoint=None):
        return Function(self, name=name, static=static, cache=cache,
                        checkpoint=checkpoint)


class FunctionSpace:
    def __init__(self, dim):
        comm = _default_comm
        if comm.size > 1:
            raise InterfaceException("Serial only")

        self._comm = comm
        self._id = new_space_id()
        self._dim = dim
        add_interface(self, FunctionSpaceInterface)

    def comm(self):
        return self._comm

    def id(self):
        return self._id

    def dim(self):
        return self._dim


class FunctionInterface(_FunctionInterface):
    def _comm(self):
        return self.space().comm()

    def _space(self):
        return self.space()

    def _id(self):
        return self.id()

    def _name(self):
        return self.name()

    def _state(self):
        return self.state()

    def _update_state(self):
        self.update_state()

    def _is_static(self):
        return self.is_static()

    def _is_cached(self):
        return self.is_cached()

    def _is_checkpointed(self):
        return self.is_checkpointed()

    def _update_caches(self, value=None):
        pass

    def _zero(self):
        self.vector()[:] = 0.0

    def _assign(self, y):
        if isinstance(y, (int, float)):
            self.vector()[:] = float(y)
        else:
            assert isinstance(y, Function)
            self.vector()[:] = y.vector()

    def _axpy(self, *args):  # self, alpha, x
        alpha, x = args
        alpha = float(alpha)
        if isinstance(x, (int, float)):
            self.vector()[:] += alpha * float(x)
        else:
            assert isinstance(x, Function)
            self.vector()[:] += alpha * x.vector()

    def _inner(self, y):
        assert isinstance(y, Function)
        return self.vector().dot(y.vector())

    def _max_value(self):
        return self.vector().max()

    def _sum(self):
        return self.vector().sum()

    def _linf_norm(self):
        return abs(self.vector()).max()

    def _local_size(self):
        return self.vector().shape[0]

    def _global_size(self):
        return self.vector().shape[0]

    def _local_indices(self):
        return slice(0, self.vector().shape[0])

    def _get_values(self):
        values = self.vector().view()
        values.setflags(write=False)
        if not np.can_cast(values, np.float64):
            raise InterfaceException("Invalid dtype")
        return values

    def _set_values(self, values):
        if not np.can_cast(values, np.float64):
            raise InterfaceException("Invalid dtype")
        if values.shape != self.vector().shape:
            raise InterfaceException("Invalid shape")
        self.vector()[:] = values

    def _new(self, name=None, static=False, cache=None, checkpoint=None):
        return Function(self.space(), name=name, static=static,
                        cache=cache, checkpoint=checkpoint)

    def _copy(self, name=None, static=False, cache=None, checkpoint=None):
        return Function(self.space(), name=name, static=static, cache=cache,
                        checkpoint=checkpoint, _data=self.vector().copy())

    def _tangent_linear(self, name=None):
        return self.tangent_linear(name=name)

    def _replacement(self):
        return self.replacement()

    def _is_replacement(self):
        return False

    def _is_real(self):
        return self.space().dim() == 1

    def _real_value(self):
        # assert is_real_function(self)
        return self.vector()[0]


class Function:
    def __init__(self, space, name=None, static=False, cache=None,
                 checkpoint=None, _data=None):
        id = new_function_id()
        if name is None:
            # Following FEniCS 2019.1.0 behaviour
            name = f"f_{id:d}"
        if cache is None:
            cache = static
        if checkpoint is None:
            checkpoint = not static

        self._space = space
        self._id = id
        self._name = name
        self._state = 0
        self._static = static
        self._cache = cache
        self._checkpoint = checkpoint
        self._replacement = None
        if _data is None:
            self._data = np.zeros(space.dim(), dtype=np.float64)
        else:
            if not np.can_cast(_data, np.float64):
                raise InterfaceException("Invalid dtype")
            self._data = _data
        add_interface(self, FunctionInterface)

    def space(self):
        return self._space

    def id(self):
        return self._id

    def name(self):
        return self._name

    def state(self):
        return self._state

    def update_state(self):
        self._state += 1

    def is_static(self):
        return self._static

    def is_cached(self):
        return self._cache

    def is_checkpointed(self):
        return self._checkpoint

    def tangent_linear(self, name=None):
        if self.is_static():
            return None
        else:
            return Function(self.space(), name=name, static=False,
                            cache=self.is_cached(),
                            checkpoint=self.is_checkpointed())

    def replacement(self):
        if self._replacement is None:
            self._replacement = Replacement(self)
        return self._replacement

    def vector(self):
        return self._data


class ReplacementInterface(_FunctionInterface):
    def _space(self):
        return self.space()

    def _id(self):
        return self.id()

    def _name(self):
        return self.name()

    def _state(self):
        return -1

    def _is_static(self):
        return self.is_static()

    def _is_cached(self):
        return self.is_cached()

    def _is_checkpointed(self):
        return self.is_checkpointed()

    def _update_caches(self, value=None):
        if value is None:
            raise InterfaceException("value required")

    def _new(self, name=None, static=False, cache=None, checkpoint=None):
        return Function(self.space(), name=name, static=static, cache=cache,
                        checkpoint=checkpoint)

    def _replacement(self):
        return self

    def _is_replacement(self):
        return True

    def _is_real(self):
        return self.space().dim() == 1


class Replacement:
    def __init__(self, x):
        self._space = x.space()
        self._id = x.id()
        self._name = x.name()
        self._static = x.is_static()
        self._cache = x.is_cached()
        self._checkpoint = x.is_checkpointed()
        add_interface(self, ReplacementInterface)

    def space(self):
        return self._space

    def id(self):
        return self._id

    def name(self):
        return self._name

    def is_static(self):
        return self._static

    def is_cached(self):
        return self._cache

    def is_checkpointed(self):
        return self._checkpoint


def _new_real_function(name=None, comm=None, static=False, cache=None,
                       checkpoint=None):
    return Function(FunctionSpace(1), name=name, static=static, cache=cache,
                    checkpoint=checkpoint)


add_new_real_function(backend, _new_real_function)


def default_comm():
    warnings.warn("default_comm is deprecated",
                  DeprecationWarning, stacklevel=2)
    return _default_comm


def RealFunctionSpace(comm=None):
    warnings.warn("RealFunctionSpace is deprecated -- "
                  "use new_real_function instead",
                  DeprecationWarning, stacklevel=2)
    return FunctionSpace(1)


def function_space_id(*args, **kwargs):
    warnings.warn("function_space_id is deprecated -- use space_id instead",
                  DeprecationWarning, stacklevel=2)
    return space_id(*args, **kwargs)


def function_space_new(*args, **kwargs):
    warnings.warn("function_space_new is deprecated -- use space_new instead",
                  DeprecationWarning, stacklevel=2)
    return space_new(*args, **kwargs)


def info(message):
    warnings.warn("info is deprecated -- use print instead",
                  DeprecationWarning, stacklevel=2)
    print(message)


def warning(message):
    warnings.warn("warning is deprecated -- use logging.warning instead",
                  DeprecationWarning, stacklevel=2)
    warnings.warn(message, RuntimeWarning)


def copy_parameters_dict(parameters):
    warnings.warn("copy_parameters_dict is deprecated -- "
                  "use copy.deepcopy instead",
                  DeprecationWarning, stacklevel=2)
    return copy.deepcopy(parameters)
