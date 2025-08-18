import cantools
import can
from binascii import hexlify

#loading the dbc file 
db=cantools.database.load_file('/home/ashish/CANBUS/DBC/chassis_kia_soul_ev.dbc')
# message=can_bus.recv()
filename='/home/ashish/CANBUS/candump-2019-01-07_195721.log'
#can_bus=can.interface.Bus('vcan0',bustype='socketcan')
# i=0
# data=open(filename)
# for lines in data:
# 	words=lines.split()
# 	id_data=words[2].split('#')
# 	print(id_data)
# 	msg=db.decode_message(id_data[0],id_data[1])  #(message.arbitration_id,message.data)
# 	print(msg)
# 	i++1
# 	if i>=10:
# 		break




# # Just checking the message arbitration id and data
can_bus = can.interface.Bus('vcan0', bustype='socketcan')
message = can_bus.recv()
print(message.arbitration_id,'------------------', message.data)

print(db.decode_message(message.arbitration_id,message.data))
print(hexlify(message.data))

print("++++++++++++++++++++++++")

data1="42, 0, 0, 7, 225, 0, 0, 0"
words=data1.split(',')
data2=""
for word in words:
	data2+=str(format(int(word),'02x'))
print(data2)
print(bytearray.fromhex(data2))
print(db.decode_message(688,bytearray.fromhex(data2)))
print("+++++++++++++++++++++++++++++++++++++")
sds="2A000007E1000000"
asds=bytearray.fromhex(sds)
print(asds)
# print(db.decode_message(688,b'*\x00\x00\x07\xe1\x00\x00\x00'))
# db.decode_message(message.arbitration_id,message.data)
# print(msg)
# print(type(message.arbitration_id),'--------',type(message.data))


# Table Format
# Time Stamp, CanID, Hex_Data, Phyiscal msg name, physical msg value, Unit