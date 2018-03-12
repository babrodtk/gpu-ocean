# -*- coding: utf-8 -*-

"""
Copyright (C) 2016  SINTEF ICT

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

#Import packages we need
import numpy as np
import pyopencl as cl #OpenCL in Python
import Common, SimWriter
import gc
from abc import ABCMeta, abstractmethod

reload(Common)

class Simulator(object):
    """
    Baseclass for different numerical schemes, all 'solving' the SW equations.
    """
    __metaclass__ = ABCMeta
    
    
    def __init__(self, \
                 cl_ctx, \
                 nx, ny, \
                 ghost_cells_x, \
                 ghost_cells_y, \
                 dx, dy, dt, \
                 g, f, r, A, \
                 t, \
                 theta, rk_order, \
                 coriolis_beta, \
                 y_zero_reference_cell, \
                 wind_stress, \
                 write_netcdf, \
                 ignore_ghostcells, \
                 offset_x, offset_y, \
                 block_width, block_height):
        """
        Setting all parameters that are common for all simulators
        """
        self.cl_ctx = cl_ctx
        #Create an OpenCL command queue
        self.cl_queue = cl.CommandQueue(self.cl_ctx)
        
        #Save input parameters
        #Notice that we need to specify them in the correct dataformat for the
        #OpenCL kernel
        self.nx = np.int32(nx)
        self.ny = np.int32(ny)
        self.ghost_cells_x = np.int32(ghost_cells_x)
        self.ghost_cells_y = np.int32(ghost_cells_y)
        self.dx = np.float32(dx)
        self.dy = np.float32(dy)
        if dt is not None:
            self.dt = np.float32(dt)
        else:
            self.dt = dt
        self.g = np.float32(g)
        self.f = np.float32(f)
        self.r = np.float32(r)
        self.coriolis_beta = np.float32(coriolis_beta)
        self.wind_stress = wind_stress
        self.y_zero_reference_cell = np.float32(y_zero_reference_cell)
        
        self.offset_x = offset_x
        self.offset_y = offset_y
        
        #Initialize time
        self.t = np.float32(t)
        
        if A is None:
            self.A = 'NA'  # Eddy viscocity coefficient
        else:
            self.A = np.float32(A)
        
        if theta is None:
            self.theta = 'NA'
        else:
            self.theta = np.float32(theta)
        if rk_order is None:
            self.rk_order = 'NA'
        else:
            self.rk_order = np.int32(rk_order)
            
        self.hasDrifters = False
        self.drifters = None
        
        # NetCDF related parameters
        self.write_netcdf = write_netcdf
        self.ignore_ghostcells = ignore_ghostcells
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.sim_writer = None
        
        #Compute kernel launch parameters
        self.local_size = (block_width, block_height) 
        self.global_size = ( \
                       int(np.ceil(self.nx / float(self.local_size[0])) * self.local_size[0]), \
                       int(np.ceil(self.ny / float(self.local_size[1])) * self.local_size[1]) \
                      ) 
    
    @abstractmethod
    def step(self, t_end=0.0):
        """
        Function which steps n timesteps
        """
        pass
    
    @abstractmethod
    def fromfilename(cls, cl_ctx, filename, cont_write_netcdf=True):
        """
        Initialize and hotstart simulation from nc-file.
        cont_write_netcdf: Continue to write the results after each superstep to a new netCDF file
        filename: Continue simulation based on parameters and last timestep in this file
        """
        pass
   
    def __del__(self):
        self.cleanUp()

    @abstractmethod
    def cleanUp(self):
        """
        Clean up function
        """
        pass
        
    def closeNetCDF(self):
        """
        Close the NetCDF file, if there is one
        """
        if self.write_netcdf:
            self.sim_writer.__exit__(0,0,0)
            self.write_netcdf = False
            self.sim_writer = None
        
    def attachDrifters(self, drifters):
        ### Do the following type of checking here:
        #assert isinstance(drifters, GPUDrifters)
        #assert drifters.isInitialized()
        
        self.drifters = drifters
        self.hasDrifters = True
        self.drifters.setCLQueue(self.cl_queue)
    
    def download(self):
        """
        Download the latest time step from the GPU
        """
        return self.cl_data.download(self.cl_queue)
    
    
    def downloadPrevTimestep(self):
        """
        Download the second-latest time step from the GPU
        """
        return self.cl_data.downloadPrevTimestep(self.cl_queue)
        
    def copyState(self, otherSim):
        """
        Copies the state ocean state (eta, hu, hv), the wind object and 
        drifters (if any) from the other simulator.
        
        This function is exposed to enable efficient re-initialization of
        resampled ocean states. This means that all parameters which can be 
        initialized/assigned a perturbation should be copied here as well.
        """
        
        assert type(otherSim) is type(self), "A simulator can only copy the state from another simulator of the same class. Here we try to copy a " + str(type(otherSim)) + " into a " + str(type(self))
        
        assert (self.ny, self.nx) == (otherSim.ny, otherSim.nx), "Simulators differ in computational domain. Self (ny, nx): " + str((self.ny, self.nx)) + ", vs other: " + ((otherSim.ny, otherSim.nx))
        
        self.cl_data.h0.copyBuffer( self.cl_queue, otherSim.cl_data.h0)
        self.cl_data.hu0.copyBuffer(self.cl_queue, otherSim.cl_data.hu0)
        self.cl_data.hv0.copyBuffer(self.cl_queue, otherSim.cl_data.hv0)
        
        self.cl_data.h1.copyBuffer( self.cl_queue, otherSim.cl_data.h1)
        self.cl_data.hu1.copyBuffer(self.cl_queue, otherSim.cl_data.hu1)
        self.cl_data.hv1.copyBuffer(self.cl_queue, otherSim.cl_data.hv1)
        
        # Question: Which parameters should we require equal, and which 
        # should become equal?
        self.wind_stress = otherSim.wind_stress
        
        if otherSim.hasDrifters and self.hasDrifters:
            self.drifters.setParticlePositions(otherSim.drifters.getParticlePositions())
            self.drifters.setObservationPosition(otherSim.drifters.getObservationPosition())
        
        
        
    def upload(self, eta0, hu0, hv0, eta1=None, hu1=None, hv1=None):
        """
        Reinitialize simulator with a new ocean state.
        """
        self.cl_data.h0.upload(self.cl_queue, eta0)
        self.cl_data.hu0.upload(self.cl_queue, hu0)
        self.cl_data.hv0.upload(self.cl_queue, hv0)
        
        if eta1 is None:
            self.cl_data.h1.upload(self.cl_queue, eta0)
            self.cl_data.hu1.upload(self.cl_queue, hu0)
            self.cl_data.hv1.upload(self.cl_queue, hv0)
        else:
            self.cl_data.h1.upload(self.cl_queue, eta1)
            self.cl_data.hu1.upload(self.cl_queue, hu1)
            self.cl_data.hv1.upload(self.cl_queue, hv1)
        

    
    