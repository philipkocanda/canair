# CANBUS_Decoder
This repository contains a CAN-BUS data logger and data decoder-parser script.
Cantools, can, binascii and numpy python-libraries have been used for these scripts.
The can_decoder.py scripts imports the DBC file and the raw data file from the argument directory. A raw data csv file as well as a text file containing physical can messages can be saved by the script.
The DBC file for Kia Soul EV is also included in the /DBC directory [Only for throttle, steering and vehicle speed messages]. 


![alt text](AV_Platform.gif) <br>
The CANBUS data decoder script was used for data collection for the Autonomous Vehicle (AV) platform project at SafeAI lab, Carnegie Mellon University. The steering, throttle and brake data was recorded and added to the dataset. An example of the recorded dataset can be observed in the gif above.
