import os
import matplotlib.pyplot as plt
import numpy as np

filename="Test1.txt"
data=open(filename)
steering_angle=[]
brake_pressure=[]
wheel_speed=[]
np.append(steering_angle,[1,2,3,3,5])
i=0
for lines in data:
	i+=1
	words=lines.split()
	if(i>=5 and i<=20):
		if(len(words)>=3):
			brake_pressure.append(())
		for word in words:
			print("{}\t".format(word),end='')
		print('\n')