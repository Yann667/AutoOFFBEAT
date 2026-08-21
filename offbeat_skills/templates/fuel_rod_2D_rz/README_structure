#------------------------------------------------------------------------------#
#                                                                              #
#                       README - folder structure                              #
#                                                                              #
#------------------------------------------------------------------------------#

This file gives an overview on the standard structure of an OFFBEAT case 
directory.

- '0/':
   |
   |- 'T' : Boundary conditions for the temperature field.
   |
   |- 'D' or 'DD' : Boundary conditions for the total or incremental displacement
   |                field.
   |
   |- 'gapGas' : information on the initial gapGas composition and pressure.

- 'constant/':
   |
   |- 'polyMesh/' : Contains mesh informations (e.g. points, faces, etc). It is
   |                created by the 'blockMesh' utility.
   |
   |- 'solverDict' : Dictionary for the solvers selection and model activation.
                     It is the main OFFBEAT dictionary. 
   
- 'system/':
   |
   |- 'blockMeshDict' : Information for the mesh building. Read by the 
   |                    'blockMesh' utility.
   |
   |- 'changeDictionaryDict' : Information on the patch boundaries to be 
   |                           modified. Read by the 'changeDictionary' utility.
   |
   |- 'controlDict' : Dicitonary for the control of the simulation parameters.
   |                  Start time, end time, adaptive time step options, etc. are
   |                  set within this dictionary. 
   |
   |- 'fvSchemes' : Settings of the discretization schemes used bu the code for 
   |               time derivatives, gradients, laplacians, etc.  
   |
   |- 'fvSolution' : Setttings for the numerical solvers used by the code. 
   |
   |- 'probes' : Position (in the mesh) of the probes used to record quantities
   |              during runtime.
   
- 'Allrun' and 'Allclean' : bash scripts used to sequentially perform a set of 
                            commands to respectively run and clean the case. 
   
- 'Residuals.gp' : gnuplot script used to visualize the residuals of the 
                     at runtime.
   
- 'plot.py' : simple python script; just an example of how you can plot the
               quantities recorded in the folder postProcessing/ (saved by the 
               probes or by the sample utility).  
   
- 'rodMaker.py' : a simple python tool to simplify the creation of a 1D, 2D
                  smeared or discrete fuel rod with the blockMesh tool. It reads
                  the rodDict dictionary and it outputs a blockMeshDict file.

