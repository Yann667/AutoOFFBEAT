from numpy import *
import matplotlib.pyplot as plt

t = []
temp = []
burnup = []

folderPath = "postProcessing/probes/0/"

with open(folderPath+'T') as tempFile, open(folderPath+'Bu') as buFile:

    tempLines = tempFile.readlines()
    for line in tempLines:
        if not(line.split()[0] == '#'):            
            t.append(float(line.split()[0])/24/3600)
            temp.append(float(line.split()[1]))

    buLines = buFile.readlines()
    for line in buLines:
        if not(line.split()[0] == '#'):            
            burnup.append(float(line.split()[1])/1000)

fig, ax1 = plt.subplots()

color = 'tab:red'
ax1.set_xlabel('Time, days')
ax1.set_ylabel('Temperature, K', color=color)
ax1.plot(t, temp, '-', label= "Temperature", color=color)
ax1.tick_params(axis='y', labelcolor=color)

ax2 = ax1.twinx()

color = 'tab:blue'
ax2.set_ylabel('Burnup, MWd/kg', color=color)
ax2.plot(t, burnup, '-.', label= "Burnup", color=color)
ax2.tick_params(axis='y', labelcolor=color)

fig.legend()
fig.tight_layout()

# plt.axis(fontsize= 30) 
# plt.xticks(fontsize=30)    
# plt.yticks(fontsize=30)

plt.show() 
