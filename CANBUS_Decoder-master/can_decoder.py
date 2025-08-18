"""
 Function load the can log file from socketcan ros package, DBC file and decode the can message
  to physical messages
  Ashish Roongta
 SAFEAI lab, Carnegie Mellon University

	-----------Documentation------------------------------

[Input]:
dbc_file: the .dbc file name of the DBC file, with the realtive directory
			eg- DBC_dir/chassis_kia_soul_ev.dbc
data_file: the file name and realtive directory of the data file
			eg- data_dir/can0_rostopic.txt
de_file: the file name for the saving the decrypted can data

"""

#----------------Importing Packages-----------------------------------------------------
import cantools
import numpy as np
import can
import re
from binascii import hexlify

def can_decoder.py(dbc_file="DBC/chassis_kia_soul_ev.dbc",
	data_file='can0_rec1_rostopic.txt',de_file='can0_decoded'):

	#----------------Loading the dbc file ---------------------
	db=cantools.database.load_file(dbc_file)

	##----------------Creating a dictionary--------------------
	ref={'seq':0,'time':1,'id':2,'is_error':3,'data':4}

	#------Loading the data log file---------------------------
	data=open(data_file,'r')
	lines=data.readlines()
	#------------------Parsing the log file--------------------
	for i,line in enumerate(lines):
		if(line.startswith("header:")):
			# Extracting data in decimal format
			words=lines[i+11].rstrip().split('[')
			datastr=words[1].rstrip().split(']')[0]

			#extracting seq
			words=lines[i+1].rstrip().split()
			seq=int(words[1])

			#extracting time
			words1=lines[i+3].rstrip().split()
			words=lines[i+4].rstrip().split()
			time=float(words1[1]+"."+words[1])

			#extracting id
			words=lines[i+6].rstrip().split()
			mid=int(words[1])

			#extracting if it is error
			words=lines[i+9].rstrip().split()
			error=(words[1]=='True')

			#appending the data to an array
			if i==0:
				table=np.array([seq,time,mid,error,datastr])
			else:
				table=np.vstack((table,[seq,time,mid,error,datastr]))

	print("++++++++++++(++++++++=++++++++++++Raw Data++++++++++=+++++++++++++++++++++++++++++++++++++")
	print("Seq","\t","Time","\t","Id","\t","is_error","\t","Data")
	print(table)
	np.save("can0_rec1_rostopic_raw",table)
	print("++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++")

	#--------------Decoding the can messages to phycial messages-------------------------------------------
	flag=0
	for i in range(len(table)):
		m_id=int(table[i,ref['id']])
		if (m_id!=688 and m_id!=1200 and m_id!= 544):
			continue
		msg_data=table[i,ref['data']].split(',')
		data_hex=""

		for subdata in msg_data:
			data_hex+=str(format(int(subdata),'02x'))
		if flag==0:
			decoded_table=np.array([float(table[i,ref['time']]),db.decode_message(int(table[i,ref['id']]),bytearray.fromhex(data_hex))])
			flag=1
		else:
			decoded_table=np.vstack((decoded_table,[float(table[i,ref['time']]),db.decode_message(int(table[i,ref['id']]),bytearray.fromhex(data_hex))]))		

	# print("Time","\t","Physical Message")
	# print(decoded_table)
	#saving the decoded file
	# np.savetxt("can0_rec1_rostopic_decoded",decoded_table)
	save_file_path=de_file+".txt"
	fileh=open(save_file_path,"w")
	for i in range(len(decoded_table)):
		if(i ==0):
			fileh.write("Time (seconds"+"\t\t"+"Phyical Message")
		fileh.write(str(decoded_table[i,0])+"\t"+str(decoded_table[i,1]))
	fileh.close()
	print("Decoded can messages saved to file: {save_file_path}")