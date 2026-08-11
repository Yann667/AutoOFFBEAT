#------------------------------------------------------------------------------#
#                                                                              #
#                            README - commands                                 #
#                                                                              #
#------------------------------------------------------------------------------#

This README file contains a list of the basic commands for the OFFBEAT 
execution. If executed sequentially from the case folder, they represent a 
step-by-step guide for the correct usage of the code.

List of commands:

--- Building the mesh ----------------------------------------------------------

1. 'blockMesh' : 
   The execution of this command builds the computational mesh based on the
   dictionary 'blockMeshDict' located into the 'system' directory.  
   This command will create the 'constant/polyMesh' folder, where all the 
   information (points, faces, cells etc.) concerning the created mesh are 
   stored in several dictionaries.

--- Changing patch type (optional) ---------------------------------------------

2. 'changeDictionary' : 
   You can change files and dictionaries in your directory by executing this 
   command which will read the file 'system/changeDictionaryDict'.

--- Running the code -----------------------------------------------------------

3. 'offbeat' : 
   This command will run OFFBEAT. A set of information (log) about the code 
   execution will be displayed in the terminal window. If the ' End ' string is 
   printed at the end of the execution, the run is succesfully terminated. 
   Please always verify the presence of the string. To store the log information
   in a file, see the 3a alternative.

3a. 'offbeat | tee log.offbeat' : 
    To save the log information in a file simultaneously with the code execution
    , the command 3a can be run. Although any name can be chosen for the log 
    file, the use of the name 'log.offbeat' will ensure the proper functioning 
    of other utilities making use of the log file.

3b. 'offbeat | tee log.offbeat & gnuplot Residuals.gp' : 
    For the convergence monitoring through the offbeat iteration, the gnuplot 
    'Residuals.gp' script can be run. This command will display an 
    auto-refreshing gnuplot windows to monitor the residual behavior.

--- PostProcessing -------------------------------------------------------------

4. 'paraFoam' : 
   This command allows the visualization of the fields computed by OFFBEAT and 
   stored in the time-step folders through the paraView software. 
   Note: only the fields related to the written time steps can be visualized.

5. 'postProcess -func "sampleDict" ' :
   This command is an example of the usage of the OpenFOAM postProcess utility.
   By setting up the "system/sampleDict" dictionary it is possible to sample 
   fields of interest in different points of the simulated domain. The output of
   the sampling wil be stored in the created 'postProcessing' folder. 

6. 'python plot.py' : 
   It is often helpful to write simple python script to automatically plot the 
   centerline temperature or other quantities, usually store in postprocessing 
   folder. The example provided in the folder plots centerline temperature 
   at the center of the rod and the correpsonding local burnup.

--- Case cleaning --------------------------------------------------------------

7. 'foamListTimes -rm':
   To erase the results of a past execution this command can be run. It will 
   delete all the stored time step directories from the case (except 0/ folder).
