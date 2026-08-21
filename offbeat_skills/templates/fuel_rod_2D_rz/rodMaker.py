######### This script helps you in the creation of a blockMeshDict for 
######### a 1D or 2D (r-z). The 2D rod can have discrete pellets or a smeared
######### column. The script reads the input from a file named 
######### 'rodDict', which should be placed in the same folder
######### as this script. Read the comment section on the input file for more
######### information about how the rod is modeled.

import math
from collections import defaultdict
import os
import re

# importing the module 
import ast 

def readRodDict(inputFile="rodDict"):
    # Reading the data from the rodDict file 
    with open(inputFile) as f: 
        data = f.read()       
    # Reconstructing the data as a dictionary 
    return ast.literal_eval(data) 

def writeBlockMeshDict(rodDict, outputFile="blockMeshDict"):
    with open(outputFile, "w") as file:
        writeHeader(file)
        writeConvertToMeters(file, rodDict['convertToMeters'])
        writeVerticesSection(file, rodDict)
        writeBlockSection(file, rodDict)
        writeEdgeSection(file, rodDict)
        writePatchSection(file, rodDict)

def writeHeader(file):
    header = [
        "/*--------------------------------*- C++ -*----------------------------------*\\",
        "| ========                 |                                                 |",
        "| \      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |",
        "|  \    /   O peration     | Version:  5.0                                   |",
        "|   \  /    A nd           | Web:      www.OpenFOAM.org                      |",
        "|    \/     M anipulation  |                                                 |",
        "\*---------------------------------------------------------------------------*/",
        "FoamFile",
        "{",
        "    version     9.0;",
        "    format      ascii;",
        "    class       dictionary;",
        "    object      blockMeshDict;",
        "}",
        "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //"
    ]
    file.write("\n".join(header) + "\n")

def writeConvertToMeters(file, convertToMeters):
    file.write(f"\nconvertToMeters {convertToMeters}; \n\n")

# Vertices section
def writeVerticesSection(file, rodDict):
    file.write("vertices \n(\n")

    # Write fuel vertices
    offset  = rodDict['offsetFuel']
    for i in range(rodDict['nBlocksFuel']):

        n = 1
        if rodDict['geometryType'] == '2Ddiscrete':
            n = rodDict['nPelletsFuel'][i]

        rIn  = rodDict['rInnerFuel'][i]
        rOut = rodDict['rOuterFuel'][i]
        h    = rodDict['heightFuel'][i]/(n)

        if(rodDict['geometryType'] == '2Ddiscrete'):
            rDish = rodDict['rDishFuel'][i]
            hChamfer = rodDict['heightChamferFuel'][i]
            wCham = rodDict['chamferWidth'][i]
            rCurvature = rodDict['rCurvatureDish'][i]

            # Calculate height of dish based on curvature radius
            angleDish = math.acos(rDish/rCurvature)
            hDish = rCurvature*(1 - math.sin(angleDish))
            
            rLand = rOut
            pelletType = 'flat'

            if rDish > 0.0 and wCham > 0.0:
                pelletType = 'dishedChamfered'
                rLand = rOut - wCham
            elif rDish > 0.0:
                pelletType = 'dished'
                rLand = rOut
            elif wCham > 0.0:
                pelletType = 'chamfered'                        
                rLand = rOut - wCham
            else:
                pelletType = 'flat'                        
                rLand = rOut

            for sectionI in range(n):
                writePelletVertices(pelletType, rodDict['wedgeAngle'], rIn, rDish, hDish, rLand, hChamfer, rOut, h, offset, file)
                offset = offset + h
        else:
            for sectionI in range(n):
                writeVertices(rodDict['wedgeAngle'], rIn, rOut, h, offset, file) 
                offset = offset + h     

    # Write clad vertices
    offset  = rodDict['offsetClad']   

    # Adjust if bottom cap is present
    if(rodDict['bottomCapHeight'] > 0):
        rIn  = rodDict['rInnerClad'][0]
        rOut = rodDict['rOuterClad'][0]
        h    = rodDict['bottomCapHeight']

        offset -= float(rodDict['bottomCapHeight'])
        writeCapVertices(rodDict['wedgeAngle'], rIn, rOut, h, offset, file)
        offset = offset + h    

    # Write clad verices between caps (if present)
    for i in range(rodDict['nBlocksClad']):    
        rIn  = rodDict['rInnerClad'][i]
        rOut = rodDict['rOuterClad'][i]
        h    = rodDict['heightClad'][i]

        writeVertices(rodDict['wedgeAngle'], rIn, rOut, h, offset, file)
        offset = offset + h

    # Adjust if bottom cap is present
    if(rodDict['topCapHeight'] > 0):
        rIn  = rodDict['rInnerClad'][rodDict['nBlocksClad']-1]
        rOut = rodDict['rOuterClad'][rodDict['nBlocksClad']-1]
        h    = rodDict['topCapHeight']

        writeCapVertices(rodDict['wedgeAngle'], rIn, rOut, h, offset, file)
        offset = offset + h

    file.write(");\n\n")

def writeVertices(wedgeAngle, rInner, rOuter, height, offset, file):
    
    #### Prepare input data ####
    wedgeAngleDegree = float(wedgeAngle)
    wedgeAngleRadiant = wedgeAngleDegree/180*math.pi
    rInner = float(rInner)
    rOuter = float(rOuter)
    height = float(height)
    offset = float(offset)

    # Prepare list of points
    pointList = []

    # Correction for wedge volume
    # Relevant only for large wedge angles
    sinAngle = math.sin(wedgeAngleRadiant/2)
    cosAngle = math.cos(wedgeAngleRadiant/2)
    correction = math.sqrt(sinAngle/(wedgeAngleRadiant/2))

    # Coordinates for vertices
    rInnerX = rInner*cosAngle*correction
    rInnerY = rInner*sinAngle*correction
    rOuterX = rOuter*cosAngle*correction
    rOuterY = rOuter*sinAngle*correction

    # Define block vertices for each block        
    pointList.append([rInnerX, -rInnerY, offset])
    pointList.append([rOuterX, -rOuterY, offset])
    pointList.append([rOuterX,  rOuterY, offset])
    pointList.append([rInnerX,  rInnerY, offset])

    pointList.append([rInnerX, -rInnerY, offset + height])
    pointList.append([rOuterX, -rOuterY, offset + height])
    pointList.append([rOuterX,  rOuterY, offset + height])
    pointList.append([rInnerX,  rInnerY, offset + height])

    # Write fuel vertices
    numberPoints = len(pointList)
    for i in range(numberPoints):
        newLine = "    (" 
        newLine += str(pointList[i][0]) + " "
        newLine += str(pointList[i][1]) + " " 
        newLine += str(pointList[i][2]) + ")" 
        newLine += "\n"
    
        file.write(newLine)

    file.write("\n")

def writePelletVertices(pelletType, wedgeAngle, rInner, rDish, hDish, rLand, hChamfer, rOuter, height, offset, file):
    
    #### Prepare input data ####
    wedgeAngleDegree = float(wedgeAngle)
    wedgeAngleRadiant = wedgeAngleDegree/180*math.pi
    rInner = float(rInner)
    rOuter = float(rOuter)
    height = float(height)
    offset = float(offset)

    # Prepare list of points
    pointList = []

    # Correction for wedge volume
    # Relevant only for large wedge angles
    sinAngle = math.sin(wedgeAngleRadiant/2)
    cosAngle = math.cos(wedgeAngleRadiant/2)
    correction = math.sqrt(sinAngle/(wedgeAngleRadiant/2))

    # Coordinates for vertices
    rInnerX = rInner*cosAngle*correction
    rInnerY = rInner*sinAngle*correction
    rDishX = rDish*cosAngle*correction
    rDishY = rDish*sinAngle*correction
    rLandX = rLand*cosAngle*correction
    rLandY = rLand*sinAngle*correction
    rOuterX = rOuter*cosAngle*correction
    rOuterY = rOuter*sinAngle*correction

    if pelletType == 'dishedChamfered':
        # Define block vertices for each block        
        pointList.append([rInnerX, -rInnerY, offset + hDish])
        pointList.append([rDishX, -rDishY, offset])
        pointList.append([rDishX,  rDishY, offset])
        pointList.append([rInnerX,  rInnerY, offset + hDish])

        pointList.append([rInnerX, -rInnerY, offset - hDish + height])
        pointList.append([rDishX, -rDishY, offset + height])
        pointList.append([rDishX,  rDishY, offset + height])
        pointList.append([rInnerX,  rInnerY, offset - hDish + height])

        pointList.append([rLandX, -rLandY, offset])
        pointList.append([rLandX,  rLandY, offset])

        pointList.append([rLandX, -rLandY, offset + height])
        pointList.append([rLandX,  rLandY, offset + height])

        pointList.append([rOuterX, -rOuterY, offset + hChamfer])
        pointList.append([rOuterX,  rOuterY, offset + hChamfer])

        pointList.append([rOuterX, -rOuterY, offset + height - hChamfer])
        pointList.append([rOuterX,  rOuterY, offset + height - hChamfer])

    elif pelletType == 'dished':
        # Define block vertices for each block        
        pointList.append([rInnerX, -rInnerY, offset + hDish])
        pointList.append([rDishX, -rDishY, offset])
        pointList.append([rDishX,  rDishY, offset])
        pointList.append([rInnerX,  rInnerY, offset + hDish])

        pointList.append([rInnerX, -rInnerY, offset - hDish + height])
        pointList.append([rDishX, -rDishY, offset + height])
        pointList.append([rDishX,  rDishY, offset + height])
        pointList.append([rInnerX,  rInnerY, offset - hDish + height])

        pointList.append([rOuterX, -rOuterY, offset])
        pointList.append([rOuterX,  rOuterY, offset])

        pointList.append([rOuterX, -rOuterY, offset + height])
        pointList.append([rOuterX,  rOuterY, offset + height])

    elif pelletType == 'chamfered':  
        # Define block vertices for each block        
        pointList.append([rInnerX, -rInnerY, offset])
        pointList.append([rLandX, -rLandY, offset])
        pointList.append([rLandX,  rLandY, offset])
        pointList.append([rInnerX,  rInnerY, offset])

        pointList.append([rInnerX, -rInnerY, offset + height])
        pointList.append([rLandX, -rLandY, offset + height])
        pointList.append([rLandX,  rLandY, offset + height])
        pointList.append([rInnerX,  rInnerY, offset + height])

        pointList.append([rOuterX, -rOuterY, offset + hChamfer])
        pointList.append([rOuterX,  rOuterY, offset + hChamfer])

        pointList.append([rOuterX, -rOuterY, offset + height - hChamfer])
        pointList.append([rOuterX,  rOuterY, offset + height - hChamfer])

    elif pelletType == 'flat':  
        # Define block vertices for each block        
        pointList.append([rInnerX, -rInnerY, offset])
        pointList.append([rOuterX, -rOuterY, offset])
        pointList.append([rOuterX,  rOuterY, offset])
        pointList.append([rInnerX,  rInnerY, offset])

        pointList.append([rInnerX, -rInnerY, offset + height])
        pointList.append([rOuterX, -rOuterY, offset + height])
        pointList.append([rOuterX,  rOuterY, offset + height])
        pointList.append([rInnerX,  rInnerY, offset + height])

    # Write fuel vertices
    numberPoints = len(pointList)
    for i in range(numberPoints):
        newLine = "    (" 
        newLine += str(pointList[i][0]) + " "
        newLine += str(pointList[i][1]) + " " 
        newLine += str(pointList[i][2]) + ")" 
        newLine += "\n"
    
        file.write(newLine)

    file.write("\n")

def writeCapVertices(wedgeAngle, rInner, rOuter, height, offset, file):
    
    #### Prepare input data ####
    wedgeAngleDegree = float(wedgeAngle)
    wedgeAngleRadiant = wedgeAngleDegree/180*math.pi
    rInner = float(rInner)
    rOuter = float(rOuter)
    height = float(height)
    offset = float(offset)

    # Prepare list of points
    pointList = []

    # Correction for wedge volume
    # Relevant only for large wedge angles
    sinAngle = math.sin(wedgeAngleRadiant/2)
    cosAngle = math.cos(wedgeAngleRadiant/2)
    correction = math.sqrt(sinAngle/(wedgeAngleRadiant/2))

    # Coordinates for vertices
    rInnerX = rInner*cosAngle*correction
    rInnerY = rInner*sinAngle*correction
    rOuterX = rOuter*cosAngle*correction
    rOuterY = rOuter*sinAngle*correction

    # Define block vertices for each block        
    pointList.append([0, 0, offset])
    pointList.append([rInnerX, -rInnerY, offset])
    pointList.append([rInnerX,  rInnerY, offset])
    pointList.append([0,  0, offset])

    pointList.append([0, 0, offset + height])
    pointList.append([rInnerX, -rInnerY, offset + height])
    pointList.append([rInnerX,  rInnerY, offset + height])
    pointList.append([0,  0, offset + height])

    # pointList.append([rInnerX, -rInnerY, offset])
    pointList.append([rOuterX, -rOuterY, offset])
    pointList.append([rOuterX,  rOuterY, offset])
    # pointList.append([rInnerX,  rInnerY, offset])

    # pointList.append([rInnerX, -rInnerY, offset + height])
    pointList.append([rOuterX, -rOuterY, offset + height])
    pointList.append([rOuterX,  rOuterY, offset + height])
    # pointList.append([rInnerX,  rInnerY, offset + height])

    # Write fuel vertices
    numberPoints = len(pointList)
    for i in range(numberPoints):
        newLine = "    (" 
        newLine += str(pointList[i][0]) + " "
        newLine += str(pointList[i][1]) + " " 
        newLine += str(pointList[i][2]) + ")" 
        newLine += "\n"
    
        file.write(newLine)

    file.write("\n")
    
# Block section
def writeBlockSection(file, rodDict):
    
    # Write fuel blocks
    startIndex = 0
    file.write("blocks \n(\n")

    for i in range(rodDict['nBlocksFuel']):

        meshR = rodDict['nCellsRFuel'][i]
        meshZ = rodDict['nCellsZFuel'][i] 
        name = rodDict['blockNameFuel'][i]

        n = 1
        if rodDict['geometryType'] == '2Ddiscrete':
            n = rodDict['nPelletsFuel'][i]

        if(rodDict['geometryType'] == '2Ddiscrete'):
            meshDishR = rodDict['nCellsRDish'][i] 
            meshLandR = rodDict['nCellsRLand'][i] 
            meshChamferR = rodDict['nCellsRChamfer'][i] 

            rDish = rodDict['rDishFuel'][i]
            wCham = rodDict['chamferWidth'][i]
            pelletType = 'flat'
            if rDish > 0.0 and wCham > 0.0:
                pelletType = 'dishedChamfered'
            elif rDish > 0.0:
                pelletType = 'dished'
            elif wCham > 0.0:
                pelletType = 'chamfered'   
            else:
                pelletType = 'flat'       

            for sectionI in range(n):
                writePelletBlock(pelletType, startIndex, name, meshDishR, meshLandR, meshChamferR, meshZ, file)
                if pelletType == 'dishedChamfered':
                    startIndex += 16
                elif pelletType == 'flat':
                    startIndex += 8
                else:
                    startIndex += 12

        else:
            for sectionI in range(n):
                writeBlock(startIndex, name, meshR, meshZ, file)
                startIndex += 8
        
    # Adjust if bottom cap is present
    if(rodDict['bottomCapHeight'] > 0):
        meshR = rodDict['nCellsRClad'][0] 
        meshRInnerPart = rodDict['nCellsRBottomCap']
        meshZ = rodDict['nCellsZBottomCap']
        name = rodDict['blockNameClad'][0] 

        writeCapBlock(startIndex, name, meshR, meshRInnerPart, meshZ, file)
        startIndex += 12

    # Write clad blocks between caps
    for i in range(rodDict['nBlocksClad']):
        meshR = rodDict['nCellsRClad'][i] 
        meshZ = rodDict['nCellsZClad'][i] 
        name = rodDict['blockNameClad'][i]
        
        writeBlock(startIndex, name, meshR, meshZ, file)
        startIndex += 8

    # Adjust if bottom cap is present
    if(rodDict['topCapHeight'] > 0):
        meshR = rodDict['nCellsRClad'][rodDict['nBlocksClad']-1] 
        meshRInnerPart = rodDict['nCellsRTopCap']
        meshZ = rodDict['nCellsZTopCap']
        name = rodDict['blockNameClad'][rodDict['nBlocksClad']-1] 

        writeCapBlock(startIndex, name, meshR, meshRInnerPart, meshZ, file)
        startIndex += 12

    file.write(");\n\n")    

def writeBlock(startIndex, zoneName, meshX, meshZ, file):

    index = startIndex
    newLine = "    hex ( " 

    for i in range(8):
        newLine += str(index) + " "
        index += 1

    newLine += ") " + str(zoneName) + " ("
    newLine +=  str(meshX) + " 1 " + str(meshZ) 
    newLine += ") simpleGrading (1 1 1)"

    file.write(newLine + "\n")

def writePelletBlock(pelletType, startIndex, zoneName, meshDishX, meshLandX, meshChamferX, meshZ, file):

    index = startIndex
    newLine = "    hex ( " 

    # First block - only block for flat pellet
    for i in range(8):
        newLine += str(index) + " "
        index += 1

    newLine += ") " + str(zoneName) + " ("
    if pelletType == 'flat' or pelletType == 'chamfered':
        newLine +=  str(meshLandX) + " 1 " + str(meshZ) 
    else:
        newLine +=  str(meshDishX) + " 1 " + str(meshZ) 
    newLine += ") simpleGrading (1 1 1)"

    file.write(newLine + "\n")

    if not(pelletType == 'flat'):
        # Write land or chamfer piece depending on pellet type
        index = startIndex
        newLine = "    hex ( " 

        newLine += str(index+1) + " "
        newLine += str(index+8) + " "
        newLine += str(index+9) + " "
        newLine += str(index+2) + " "

        newLine += str(index+5) + " "
        newLine += str(index+10) + " "
        newLine += str(index+11) + " "
        newLine += str(index+6) + " "

        newLine += ") " + str(zoneName) + " ("
        if pelletType == 'chamfered':
            newLine +=  str(meshChamferX) + " 1 " + str(meshZ) 
        else:
            newLine +=  str(meshLandX) + " 1 " + str(meshZ) 
        newLine += ") simpleGrading (1 1 1)"

        file.write(newLine + "\n")

    if pelletType == 'dishedChamfered':
        # Write chamfer piece
        index = startIndex
        newLine = "    hex ( " 

        newLine += str(index+8) + " "
        newLine += str(index+12) + " "
        newLine += str(index+13) + " "
        newLine += str(index+9) + " "

        newLine += str(index+10) + " "
        newLine += str(index+14) + " "
        newLine += str(index+15) + " "
        newLine += str(index+11) + " "

        newLine += ") " + str(zoneName) + " ("
        newLine +=  str(meshChamferX) + " 1 " + str(meshZ) 
        newLine += ") simpleGrading (1 1 1)"

        file.write(newLine + "\n")
        
def writeCapBlock(startIndex, zoneName, meshX, meshXInnerPart, meshZ, file):

    index = startIndex
    newLine = "    hex ( " 

    for i in range(8):
        newLine += str(index) + " "
        index += 1

    newLine += ") " + str(zoneName) + " ("
    newLine +=  str(meshXInnerPart) + " 1 " + str(meshZ) 
    newLine += ") simpleGrading (1 1 1)"

    file.write(newLine + "\n")

    # Write second piece
    index = startIndex
    newLine = "    hex ( " 

    newLine += str(index+1) + " "
    newLine += str(index+8) + " "
    newLine += str(index+9) + " "
    newLine += str(index+2) + " "

    newLine += str(index+5) + " "
    newLine += str(index+10) + " "
    newLine += str(index+11) + " "
    newLine += str(index+6) + " "

    newLine += ") " + str(zoneName) + " ("
    newLine +=  str(meshX) + " 1 " + str(meshZ) 
    newLine += ") simpleGrading (1 1 1)"

    file.write(newLine + "\n")

# Edge section
def writeEdgeSection(file, rodDict):
    # Write fuel edges
    startIndex = 0
    file.write("edges \n(\n")

    offset = rodDict['offsetFuel']
    if(rodDict['geometryType'] == '2Ddiscrete'):

        for i in range(rodDict['nBlocksFuel']):
            
            rDish = rodDict['rDishFuel'][i]
            wCham = rodDict['chamferWidth'][i]

            pelletType = 'flat'
            if rDish > 0.0 and wCham > 0.0:
                pelletType = 'dishedChamfered'
            elif rDish > 0.0:
                pelletType = 'dished'
            elif wCham > 0.0:
                pelletType = 'chamfered'   
            else:
                pelletType = 'flat'     

            if pelletType == 'dished' or pelletType == 'dishedChamfered':
                n = rodDict['nPelletsFuel'][i]

                rDish = rodDict['rDishFuel'][i]
                h     = rodDict['heightFuel'][i]/(n)

                rCurvature = rodDict['rCurvatureDish'][i]

                # Calculate height of dish based on curvature radius
                angleDish = math.acos(rDish/rCurvature)

                # Calculate height of arc point based on curvature radius
                angleArcPoint = math.acos(rDish/rCurvature/2.0)
                hArcPoint = rCurvature*(math.sin(angleArcPoint) - math.sin(angleDish))
                rArcPoint = rDish/2.0

                for sectionI in range(n):
                    writeFuelEdge(startIndex, rodDict['wedgeAngle'], rArcPoint, hArcPoint, h, offset, file)
                    offset = offset + h

                    if pelletType == 'dishedChamfered':
                        startIndex += 16
                    else:
                        startIndex += 12

            elif pelletType == 'chamfered':
                n = rodDict['nPelletsFuel'][i]
                h     = rodDict['heightFuel'][i]/(n)

                for sectionI in range(n):
                    offset = offset + h
                    startIndex += 12

            elif pelletType == 'flat':
                n = rodDict['nPelletsFuel'][i]
                h     = rodDict['heightFuel'][i]/(n)

                for sectionI in range(n):
                    offset = offset + h
                    startIndex += 8


    file.write(");\n\n")

def writeFuelEdge(startIndex, wedgeAngle, rArcPoint, hArcPoint, height, offset, file):
    
    #### Prepare input data ####
    wedgeAngleDegree = float(wedgeAngle)
    wedgeAngleRadiant = wedgeAngleDegree/180*math.pi
    rArcPoint = float(rArcPoint)
    hArcPoint = float(hArcPoint)
    height = float(height)
    offset = float(offset)
    index = int(startIndex)

    if(hArcPoint > 1e-9):

        # Correction for wedge volume
        # Relevant only for large wedge angles
        sinAngle = math.sin(wedgeAngleRadiant/2)
        cosAngle = math.cos(wedgeAngleRadiant/2)
        correction = math.sqrt(sinAngle/(wedgeAngleRadiant/2))

        # Coordinates for edge arc point
        rArcPointX = rArcPoint*cosAngle*correction
        rArcPointY = rArcPoint*sinAngle*correction

        # Write fuel edges
        newLine = "    arc " + str(index + 0) + " " + str(index + 1)
        newLine += "(" 
        newLine += str(rArcPointX) + " "
        newLine += str(-rArcPointY) + " " 
        newLine += str(hArcPoint + offset) + ")" 
        newLine += "\n"

        file.write(newLine)

        newLine = "    arc " + str(index + 3) + " " + str(index + 2)
        newLine += "(" 
        newLine += str(rArcPointX) + " "
        newLine += str(rArcPointY) + " " 
        newLine += str(hArcPoint + offset) + ")" 
        newLine += "\n"

        file.write(newLine)

        newLine = "    arc " + str(index + 4) + " " + str(index + 5)
        newLine += "(" 
        newLine += str(rArcPointX) + " "
        newLine += str(-rArcPointY) + " " 
        newLine += str(height - hArcPoint + offset) + ")" 
        newLine += "\n"

        file.write(newLine)

        newLine = "    arc " + str(index + 7) + " " + str(index + 6)
        newLine += "(" 
        newLine += str(rArcPointX) + " "
        newLine += str(rArcPointY) + " " 
        newLine += str(height - hArcPoint + offset) + ")" 
        newLine += "\n"

        file.write(newLine)

        file.write("\n")
     
# Patch section     
def writePatchSection(file, rodDict):

    # Collect boundaries into patchDict (defaultDict allows to append to entries)
    patchDict = defaultdict(list)

    # Prepare mergePatchDict (one slave patch for each master patch)
    mergePatchDict = defaultdict(list)
    
    # Initialize index
    startIndex = 0

    # Collect fuel patches
    startIndex = collectFuelPatches(rodDict, patchDict, mergePatchDict, startIndex)

    # Collect fuel patches
    startIndex = collectCladPatches(rodDict, patchDict, mergePatchDict, startIndex)

    # Write boundaries
    file.write("boundary \n(\n")
    for patchName in patchDict:
        patchFaces = patchDict[patchName]
        writePatch(patchName, patchFaces, file)

    file.write(");\n\n")

    # Write mergePatchPairs
    file.write("mergePatchPairs \n(\n")
    for masterPatchName in mergePatchDict:
        slavePatchName = mergePatchDict[masterPatchName]
        file.write("\t(")
        file.write(masterPatchName + " " + slavePatchName)
        file.write(")\n")

    file.write(");\n\n")

def collectFuelPatches(rodDict, patchDict, mergePatchDict, startIndex):
    
    globalFuelSectionIndex = 1

    totalSectionsFuel = rodDict['nBlocksFuel']
    if rodDict['geometryType'] == '2Ddiscrete':
        totalSectionsFuel = sum(rodDict['nPelletsFuel'])

    for iBlock in range(rodDict['nBlocksFuel']):

        pelletType = 'flat'
        n = 1

        if rodDict['geometryType'] == '2Ddiscrete':
            n = rodDict['nPelletsFuel'][iBlock]
            rDish = rodDict['rDishFuel'][iBlock]
            wCham = rodDict['chamferWidth'][iBlock]

            if rDish > 0.0 and wCham > 0.0:
                pelletType = 'dishedChamfered'
            elif rDish > 0.0:
                pelletType = 'dished'
            elif wCham > 0.0:
                pelletType = 'chamfered'   
            else:
                pelletType = 'flat'  

        for sectionI in range(n):

            i = startIndex

            # Collect bottom boundary
            bottomName  = 'fuelBottom'
            bottomType  = 'patch'
            bottomNeig  = 'none'
            bottomOwner = 'false'

            if globalFuelSectionIndex > 1:
                bottomName += '_' + str(globalFuelSectionIndex)

                if(not(rodDict['mergeFuelPatchPairs'])):
                    bottomType  = 'regionCoupledOFFBEAT'
                    bottomNeig  = 'fuelTop_' + str(globalFuelSectionIndex-1)

            elif(rodDict['bottomCapHeight'] > 0):
                bottomType  = 'regionCoupledOFFBEAT'
                bottomNeig  = 'bottomCapInner'
                bottomOwner = 'true'

            if rodDict['geometryType'] == '1D': bottomType = 'empty'

            # Collect bottom dish/chamfer boundaries
            dishChamfBottomName  = 'fuelBottom'
            dishChamfBottomType  = 'patch'
            dishChamfBottomNeig  = 'none'
            dishChamfBottomOnwer = 'false'

            if globalFuelSectionIndex > 1:
                dishChamfBottomName = 'fuelBottom_' + str(globalFuelSectionIndex)

                if(not(rodDict['mergeFuelPatchPairs'])): 
                    dishChamfBottomType = 'regionCoupledOFFBEAT'
                    dishChamfBottomNeig = 'fuelTop_' + str(globalFuelSectionIndex-1)

            elif(rodDict['bottomCapHeight'] > 0):
                dishChamfBottomType  = 'regionCoupledOFFBEAT'
                dishChamfBottomNeig  = 'bottomCapInner'
                dishChamfBottomOnwer = 'true'

            # Collect top boundary
            topName  = 'fuelTop'
            topType  = 'patch'
            topNeig  = 'none'
            topOwner = 'true'

            if globalFuelSectionIndex < totalSectionsFuel:
                topName += '_' + str(globalFuelSectionIndex)

                if(not(rodDict['mergeFuelPatchPairs'])):
                    topType  = 'regionCoupledOFFBEAT'
                    topNeig  = 'fuelBottom_' + str(globalFuelSectionIndex+1)
                else:
                    mergePatchDict[topName] = 'fuelBottom_' + str(globalFuelSectionIndex+1)

            if rodDict['geometryType'] == '1D': topType = 'empty'

            # Collect top dish/chamfer boundaries
            dishChamfTopName  = 'fuelTop'
            dishChamfTopType  = 'patch'
            dishChamfTopNeig  = 'none'
            dishChamfTopOwner = 'true'

            if globalFuelSectionIndex < totalSectionsFuel:
                dishChamfTopName = 'fuelTop_' + str(globalFuelSectionIndex)

                if(not(rodDict['mergeFuelPatchPairs'])): 
                    dishChamfTopType  = 'regionCoupledOFFBEAT'
                    dishChamfTopNeig  = 'fuelBottom_' + str(globalFuelSectionIndex+1)

            globalFuelSectionIndex += 1

            # Collect inner boundary
            innerName  = 'fuelInner'
            innerType  = 'patch' 
            innerNeig  = 'none'
            innerOwner = 'false'

            # Collect outer boundary
            outerName  = 'fuelOuter'
            outerType  = 'regionCoupledOFFBEAT' 
            outerNeig  = 'cladInner' 
            outerOwner = 'true'

            # Collect wedge boundaries
            wedgeFrontName = 'fuelFront'
            wedgeBackName  = 'fuelBack'

            # Bottom
            if(rodDict['geometryType'] == '2Ddiscrete'):

                if pelletType == 'flat':
                    patchDict[str(bottomName)].append(
                        [bottomType, bottomNeig, bottomOwner, i+0, i+3, i+2, i+1])

                elif pelletType == 'dished':   
                    patchDict[str(dishChamfBottomName)].append(
                        [dishChamfBottomType, dishChamfBottomNeig, dishChamfBottomOnwer, i+0, i+3, i+2, i+1])             
                    patchDict[str(bottomName)].append(
                        [bottomType, bottomNeig, bottomOwner, i+1, i+2, i+9, i+8])

                elif pelletType == 'chamfered':            
                    patchDict[str(bottomName)].append(
                        [bottomType, bottomNeig, bottomOwner, i+0, i+3, i+2, i+1])
                    patchDict[str(dishChamfBottomName)].append(
                        [dishChamfBottomType, dishChamfBottomNeig, dishChamfBottomOnwer, i+1, i+2, i+9, i+8]) 

                elif pelletType == 'dishedChamfered':
                    patchDict[str(dishChamfBottomName)].append(
                        [dishChamfBottomType, dishChamfBottomNeig, dishChamfBottomOnwer, i+0, i+3, i+2, i+1]) 
                    patchDict[str(bottomName)].append(
                        [bottomType, bottomNeig, bottomOwner, i+1, i+2, i+9, i+8])
                    patchDict[str(dishChamfBottomName)].append(
                        [dishChamfBottomType, dishChamfBottomNeig, dishChamfBottomOnwer, i+8, i+9, i+13, i+12])
            else:
                patchDict[str(bottomName)].append(
                    [bottomType, bottomNeig, bottomOwner, i+0, i+3, i+2, i+1 ])

            # Top
            if(rodDict['geometryType'] == '2Ddiscrete'):

                if pelletType == 'flat':
                    patchDict[str(topName)].append(
                        [topType, topNeig, topOwner, i+4, i+5, i+6, i+7])

                elif pelletType == 'dished':   
                    patchDict[str(dishChamfTopName)].append(
                        [dishChamfTopType, dishChamfTopNeig, dishChamfTopOwner, i+4, i+5, i+6, i+7])             
                    patchDict[str(topName)].append(
                        [topType, topNeig, topOwner, i+5, i+10, i+11, i+6])

                elif pelletType == 'chamfered':            
                    patchDict[str(topName)].append(
                        [topType, topNeig, topOwner, i+4, i+5, i+6, i+7])
                    patchDict[str(dishChamfTopName)].append(
                        [dishChamfTopType, dishChamfTopNeig, dishChamfTopOwner, i+5, i+10, i+11, i+6]) 

                elif pelletType == 'dishedChamfered':
                    patchDict[str(dishChamfTopName)].append(
                        [dishChamfTopType, dishChamfTopNeig, dishChamfTopOwner, i+4, i+5, i+6, i+7]) 
                    patchDict[str(topName)].append(
                        [topType, topNeig, topOwner, i+5, i+10, i+11, i+6])
                    patchDict[str(dishChamfTopName)].append(
                        [dishChamfTopType, dishChamfTopNeig, dishChamfTopOwner, i+10, i+14, i+15, i+11])
            else:
                patchDict[str(topName)].append(
                    [topType, topNeig, topOwner, i+4, i+5, i+6, i+7]) 

            # Inner
            if(rodDict['rInnerFuel'][iBlock] > 0.0):
                patchDict[str(innerName)].append(
                    [innerType, innerNeig, innerOwner, i+0, i+4, i+7, i+3])   

            # Outer
            if rodDict['geometryType'] == '2Ddiscrete':
                
                if pelletType == 'dishedChamfered':
                    patchDict[str(outerName)].append(
                        [outerType, outerNeig, outerOwner, i+12, i+13, i+15, i+14])    
                
                elif pelletType == 'dished' or pelletType == 'chamfered':
                    patchDict[str(outerName)].append(
                        [outerType, outerNeig, outerOwner, i+8, i+9, i+11, i+10])    
                
                elif pelletType == 'flat':
                    patchDict[str(outerName)].append(
                        [outerType, outerNeig, outerOwner, i+1, i+2, i+6, i+5])    
            else:
                patchDict[str(outerName)].append(
                    [outerType, outerNeig, outerOwner, i+1, i+2, i+6, i+5])             

            # Wedge
            if(rodDict['geometryType'] == '2Ddiscrete'):

                patchDict[str(wedgeFrontName)].append(
                    ["wedge", "none", "false", i+0, i+1, i+5, i+4]) 

                if pelletType == 'dished' or pelletType == 'chamfered' or pelletType == 'dishedChamfered':
                    patchDict[str(wedgeFrontName)].append(
                        ["wedge", "none", "false", i+1, i+8, i+10, i+5]) 

                if pelletType == 'dishedChamfered':
                    patchDict[str(wedgeFrontName)].append(
                        ["wedge", "none", "false", i+8, i+12, i+14, i+10])  
            else:
                patchDict[str(wedgeFrontName)].append(
                    ["wedge", "none", "false", i+0, i+1, i+5, i+4]) 

            if(rodDict['geometryType'] == '2Ddiscrete'):

                patchDict[str(wedgeBackName)].append(
                    ["wedge", "none", "false", i+3, i+7, i+6, i+2])

                if pelletType == 'dished' or pelletType == 'chamfered' or pelletType == 'dishedChamfered':            
                    patchDict[str(wedgeBackName)].append(
                        ["wedge", "none", "false", i+2, i+6, i+11, i+9])

                if pelletType == 'dishedChamfered':
                    patchDict[str(wedgeBackName)].append(
                        ["wedge", "none", "false", i+9, i+11, i+15, i+13]) 
            else:
                patchDict[str(wedgeBackName)].append(
                    ["wedge", "none", "false", i+3, i+7, i+6, i+2])

            if rodDict['geometryType'] == '2Ddiscrete':

                if pelletType == 'flat':
                    startIndex += 8
                elif pelletType == 'dishedChamfered':
                    startIndex += 16
                else:
                    startIndex += 12
            else:
                startIndex += 8    

    return startIndex

def collectCladPatches(rodDict, patchDict, mergePatchDict, startIndex):
    
    globalCladSectionIndex = 1
    nBlocksClad = rodDict['nBlocksClad']

    bottomCap = False
    if(rodDict['bottomCapHeight'] > 0):
        bottomCap = True
        nBlocksClad += 1

    topCap = False
    if(rodDict['topCapHeight'] > 0):
        topCap = True
        nBlocksClad += 1
    
    for iBlock in range(nBlocksClad):

        i = startIndex

        # Collect bottom boundary
        bottomName  = 'cladBottom'
        bottomType  = 'patch'
        bottomNeig  = 'none'
        bottomOwner = 'false'

        if globalCladSectionIndex > 1:
            bottomName += '_' + str(globalCladSectionIndex)

            if(not(rodDict['mergeCladPatchPairs'])):
                bottomType  = 'regionCoupledOFFBEAT'
                bottomNeig  = 'cladTop_' + str(globalCladSectionIndex-1)

        if rodDict['geometryType'] == '1D': bottomType = 'empty'

        # Collect top boundary
        topName  = 'cladTop'
        topType  = 'patch'
        topNeig  = 'none'
        topOwner = 'true'

        if globalCladSectionIndex < nBlocksClad:
            topName += '_' + str(globalCladSectionIndex)

            if(not(rodDict['mergeCladPatchPairs'])):
                topType  = 'regionCoupledOFFBEAT'
                topNeig  = 'cladBottom_' + str(globalCladSectionIndex+1)
            else:
                mergePatchDict[topName] = 'cladBottom_' + str(globalCladSectionIndex+1)

        if rodDict['geometryType']  == '1D': topType = 'empty'

        globalCladSectionIndex += 1

        # Collect bottom cap inner boundary
        bottomCapInnerName  = 'bottomCapInner'
        bottomCapInnerType  = 'regionCoupledOFFBEAT'
        bottomCapInnerNeig  = 'fuelBottom'
        bottomCapInnerOwner = 'false'

        # Collect top cap inner boundary
        topCapInnerName  = 'topCapInner'
        topCapInnerType  = 'patch'
        topCapInnerNeig  = 'none'
        topCapInnerOwner = 'true'

        # Collect inner boundary
        innerName  = 'cladInner'
        innerType  = 'regionCoupledOFFBEAT' 
        innerNeig  = 'fuelOuter'
        innerOwner = 'false'

        # Collect outer boundary
        outerName  = 'cladOuter'
        outerType  = 'patch' 
        outerNeig  = 'none' 
        outerOwner = 'false'

        # Collect wedge boundaries
        wedgeFrontName = 'cladFront'
        wedgeBackName  = 'cladBack'

        # Collect bottom
        if(bottomCap and iBlock==0):
            patchDict[str(bottomName)].append(
                [bottomType, bottomNeig, bottomOwner, i+0, i+3, i+2, i+1 ])
            patchDict[str(bottomName)].append(
                [bottomType, bottomNeig, bottomOwner, i+1, i+2, i+9, i+8 ])
        elif(topCap and iBlock==nBlocksClad-1):
            patchDict[str(bottomName)].append(
                [bottomType, bottomNeig, bottomOwner, i+1, i+2, i+9, i+8 ])
        else:
            patchDict[str(bottomName)].append(
                [bottomType, bottomNeig, bottomOwner, i+0, i+3, i+2, i+1 ])

        # Collect top cap inner
        if(topCap and iBlock==nBlocksClad-1):
            patchDict[str(topCapInnerName)].append(
                [topCapInnerType, topCapInnerNeig, topCapInnerOwner, i+0, i+3, i+2, i+1 ])

        # Collect top
        if(topCap and iBlock==nBlocksClad-1):
            patchDict[str(topName)].append(
                [topType, topNeig, topOwner, i+4, i+5, i+6, i+7]) 
            patchDict[str(topName)].append(
                [topType, topNeig, topOwner, i+5, i+10, i+11, i+6]) 
        elif(bottomCap and iBlock==0):
            patchDict[str(topName)].append(
                [topType, topNeig, topOwner, i+5, i+10, i+11, i+6]) 
        else:
            patchDict[str(topName)].append(
                [topType, topNeig, topOwner, i+4, i+5, i+6, i+7]) 

        # Collect top cap inner
        if(bottomCap and iBlock==0):
            patchDict[str(bottomCapInnerName)].append(
                [bottomCapInnerType, bottomCapInnerNeig, bottomCapInnerOwner, i+4, i+5, i+6, i+7 ])

        # Collect inner
        if(not((bottomCap and iBlock==0) or (topCap and iBlock==nBlocksClad-1))):
            patchDict[str(innerName)].append(
                [innerType, innerNeig, innerOwner, i+0, i+4, i+7, i+3])   

        # Collect outer
        if((bottomCap and iBlock==0) or (topCap and iBlock==nBlocksClad-1)):
            patchDict[str(outerName)].append(
                [outerType, outerNeig, outerOwner, i+8, i+9, i+11, i+10])   
        else:
            patchDict[str(outerName)].append(
                [outerType, outerNeig, outerOwner, i+1, i+2, i+6, i+5])    

        # Collect wedgeFront
        if((bottomCap and iBlock==0) or (topCap and iBlock==nBlocksClad-1)):
            patchDict[str(wedgeFrontName)].append(
                ["wedge", "none", "false", i+0, i+1, i+5, i+4]) 
            patchDict[str(wedgeFrontName)].append(
                ["wedge", "none", "false", i+1, i+8, i+10, i+5]) 
        else:
            patchDict[str(wedgeFrontName)].append(
                ["wedge", "none", "false", i+0, i+1, i+5, i+4]) 

        # Collect wedgeBackt
        if((bottomCap and iBlock==0) or (topCap and iBlock==nBlocksClad-1)):
            patchDict[str(wedgeBackName)].append(
                ["wedge", "none", "false", i+3, i+7, i+6, i+2])
            patchDict[str(wedgeBackName)].append(
                ["wedge", "none", "false", i+2, i+6, i+11, i+9])
        else:
            patchDict[str(wedgeBackName)].append(
                ["wedge", "none", "false", i+3, i+7, i+6, i+2])
        
        if((bottomCap and iBlock==0) or (topCap and iBlock==nBlocksClad-1)):
            startIndex += 12
        else:
            startIndex += 8

    return startIndex
                
def writePatch(patchName, patchFaces, file):

    file.write("    " + str(patchName) + "\n")
    file.write("    {" + "\n")
    file.write("        type " + patchFaces[0][0] + ";\n")
    
    if(patchFaces[0][0] == "regionCoupledOFFBEAT" ):
        file.write("        neighbourPatch " + patchFaces[0][1] + ";\n")
        file.write("        neighbourRegion region0;\n")
        file.write("        owner " + patchFaces[0][2] +";\n")
        if(patchName == "cladInner" or patchName == "fuelOuter"):
            if(not('updateAMI' in rodDict.keys()) or (rodDict['updateAMI'])):
                file.write("        updateAMI true;\n")
            else:
                file.write("        updateAMI false;\n")
        else:
            file.write("        updateAMI false;\n")

    file.write("        faces " + "\n")
    file.write("        (" + "\n")

    # Write face (there might be more than one per patch)
    for face in patchFaces:

        v1 = face[3]
        v2 = face[4]
        v3 = face[5]
        v4 = face[6]

        file.write("            (" + str(v1) + " "
                                    + str(v2) + " "
                                    + str(v3) + " "
                                    + str(v4)  + ")" + "\n")

    file.write("        );" + "\n")
    file.write("    }" + "\n\n")

################################
############# MAIN #############
################################

rodDict = readRodDict('rodDict')
writeBlockMeshDict(rodDict, 'blockMeshDict')
  










