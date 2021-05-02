# Copyright (c) 2021, Tom Westerhout
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

__version__ = "0.1.0.0"
__author__ = "Tom Westerhout <14264576+twesterhout@users.noreply.github.com>"

import ctypes
from ctypes import (
    POINTER,
    byref,
    c_double,
    c_uint32,
    c_uint64,
    c_void_p,
)
import numpy as np
import scipy.sparse
import os
import subprocess
import sys
from typing import List, Optional, Tuple, Union
import warnings
import weakref


# Enable import warnings
warnings.filterwarnings("default", category=ImportWarning)


def __library_name() -> str:
    if sys.platform == "linux":
        extension = ".so"
    elif sys.platform == "darwin":
        extension = ".dylib"
    else:
        raise ImportError("Unsupported platform: {}".format(sys.platform))
    return "libising_ground_state{}".format(extension)


def __package_path() -> str:
    """Get current package installation path."""
    return os.path.dirname(os.path.realpath(__file__))


def __load_shared_library():
    """Load C library."""
    libname = __library_name()
    # First, try the current directory.
    prefix = __package_path()
    if os.path.exists(os.path.join(prefix, libname)):
        return ctypes.CDLL(os.path.join(prefix, libname))
    # Next, try using conda
    if os.path.exists(os.path.join(sys.prefix, "conda-meta")):
        prefix = os.path.join(sys.prefix, "lib")
        try:
            return ctypes.CDLL(os.path.join(prefix, libname))
        except:
            warnings.warn(
                "Using python from Conda, but '{}' library was not found in "
                "the current environment. Will try pkg-config now...".format(libname),
                ImportWarning,
            )
    # Finally, try to determine the prefix using pkg-config
    result = subprocess.run(
        ["pkg-config", "--variable=libdir", "ising_ground_state"], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise ImportError("Failed to load ising_ground_state C library")
    prefix = result.stdout.strip()
    return ctypes.CDLL(os.path.join(prefix, __library_name()))


_lib = __load_shared_library()


def __preprocess_library():
    # fmt: off
    info = [
        ("sa_init", [], None),
        ("sa_exit", [], None),
        ("sa_create_hamiltonian", [c_uint32, POINTER(c_uint32), POINTER(c_uint32), POINTER(c_double),
                                   c_uint32, POINTER(c_double)], c_void_p),
        ("sa_destroy_hamiltonian", [c_void_p], None),
        ("sa_find_ground_state", [c_void_p, POINTER(c_uint64), c_uint32,
                                  c_uint32, c_double, c_double, POINTER(c_uint64), POINTER(c_double)], None),
    ]
    # fmt: on
    for (name, argtypes, restype) in info:
        f = getattr(_lib, name)
        f.argtypes = argtypes
        f.restype = restype


__preprocess_library()
_lib.sa_init()



def _create_hamiltonian(exchange, field):
    if not isinstance(exchange, scipy.sparse.spmatrix):
        raise TypeError("'exchange' must be a sparse matrix, but got {}".format(type(exchange)))
    if not isinstance(exchange, scipy.sparse.coo_matrix):
        warnings.warn(
            "ising_ground_state.anneal works with sparse matrices in COO format, but 'exchange' is "
            "not. A copy of 'exchange' will be created with proper format. This might incur some "
            "performance overhead."
        )
        exchange = scipy.sparse.coo_matrix(exchange)

    field = np.asarray(field, dtype=np.float64, order="C")
    if field.ndim != 1:
        raise ValueError("'field' must be a vector, but got a {}-dimensional array".format(field.ndim))
    if exchange.shape != (len(field), len(field)):
        raise ValueError("dimensions of 'exchange' and 'field' do not match: {} vs {}".format(exchange.shape, len(field)))

    row_indices = np.asarray(exchange.row, dtype=np.uint32, order="C")
    column_indices = np.asarray(exchange.col, dtype=np.uint32, order="C")
    elements = np.asarray(exchange.data, dtype=np.float64, order="C")
    return _lib.sa_create_hamiltonian(
        exchange.nnz,
        row_indices.ctypes.data_as(POINTER(c_uint32)),
        column_indices.ctypes.data_as(POINTER(c_uint32)),
        elements.ctypes.data_as(POINTER(c_double)),
        len(field),
        field.ctypes.data_as(POINTER(c_double)),
    )


class Hamiltonian:
    def __init__(self, exchange: scipy.sparse.spmatrix, field: np.ndarray):
        self._payload = _create_hamiltonian(exchange, field)
        self._finalizer = weakref.finalize(self, _lib.sa_destroy_hamiltonian, self._payload)
        self.shape = exchange.shape
        self.dtype = np.float64


def anneal(hamiltonian: Hamiltonian, seed: int = 46, number_sweeps: int = 2000, beta0: float = 0.1, beta1: float = 20000.0):
    if not isinstance(hamiltonian, Hamiltonian):
        raise TypeError("'hamiltonian' must be a Hamiltonian, but got {}".format(type(hamiltonian)))
    assert number_sweeps > 0
    (n, _) = hamiltonian.shape
    configuration = np.zeros((n + 63) // 64, dtype=np.uint64)
    energy = c_double()
    _lib.sa_find_ground_state(
        hamiltonian._payload,
        None,
        seed,
        number_sweeps,
        beta0,
        beta1,
        configuration.ctypes.data_as(POINTER(c_uint64)),
        byref(energy)
    )
    return configuration, energy.value


def _load_ground_state(filename: str):
    import h5py

    with h5py.File(filename, "r") as f:
        ground_state = f["/hamiltonian/eigenvectors"][:]
        ground_state = ground_state.squeeze()
        energy = f["/hamiltonian/eigenvalues"][0]
        basis_representatives = f["/basis/representatives"][:]
    return ground_state, energy, basis_representatives


def _load_basis_and_hamiltonian(filename: str):
    import lattice_symmetries as ls
    import yaml

    with open(filename, "r") as f:
        config = yaml.load(f, Loader=yaml.SafeLoader)
    basis = ls.SpinBasis.load_from_yaml(config["basis"])
    hamiltonian = ls.Operator.load_from_yaml(config["hamiltonian"], basis)
    return basis, hamiltonian


def classical_ising_model(spins, hamiltonian):
    basis = hamiltonian.basis
    coo_matrix = []
    for i in spins:
        (js, cs) = hamiltonian.apply(int(i))
        proper_i = basis.index(int(i))
        js = js[:, 0]
        for j, c in zip(js, cs):
            assert c.imag == 0
            proper_j = basis.index(int(j))
            coo_matrix.append((proper_i, proper_j, c.real))
    return coo_matrix


def test_anneal():
    ground_state, E, representatives = _load_ground_state("/home/tom/src/spin-ed/data/heisenberg_kagome_16.h5")
    basis, hamiltonian = _load_basis_and_hamiltonian("/home/tom/src/spin-ed/example/heisenberg_kagome_16.yaml")
    basis.build(representatives)
    print(E)
    matrix = []
    for (i, j, c) in classical_ising_model(representatives, hamiltonian):
        coupling = c * abs(ground_state[i]) * abs(ground_state[j])
        if abs(coupling) > 1e-10:
            matrix.append((i, j, coupling))
    matrix = scipy.sparse.coo_matrix(([t[2] for t in matrix], ([t[0] for t in matrix], [t[1] for t in matrix])))
    field = np.zeros(matrix.shape[0], dtype=np.float64)
    print("Running annealing...")
    print(anneal(Hamiltonian(matrix, field)))

