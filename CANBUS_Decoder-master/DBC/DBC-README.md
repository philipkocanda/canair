# DBC Files


### Usage

####  Steering angle control

DriveKit enables steering angle control via the steering angle command message.
The command message requires three fields: Enable/Disable, Angle, and Maximum
Velocity.

Do not use steering angle commands and torque commands together -- they are
mutually exclusive.

|              Signal             |   Value Table   |  Units  |
|---------------------------------|-----------------|---------|
|     steering_angle_cmd_flags    | Disable, Enable |   N/A   |
|     steering_angle_cmd_angle    |       N/A       | Degrees |
| steering_angle_cmd_max_velocity |       N/A       | Degrees |

A steering angle report is published every 20 ms to show the status of the angle
controller.

|            Signal            |                            Value Table                             |
|------------------------------|--------------------------------------------------------------------|
| steering_angle_report_flags  |                          Enable, Disable                           |
| steering_angle_report_errors | Angle Missing, Angle Irrational, Module Disabled, Command Rejected |



##### Example

In this example we first enable the steering torque controller by sending a
steering enable message.

|     Message     | steering_enable_magic |
|-----------------|-----------------------|
| STEERING_ENABLE | Default Magic Number  |

Now we can send steering angle command to enable with the desired angle no
faster than 300 degrees per seconds.

|        Message         | steering_angle_cmd_flags | steering_angle_cmd_angle | steering_angle_cmd_max_velocity |
|------------------------|--------------------------|--------------------------|---------------------------------|
| STEERING_ANGLE_COMMAND |          Enable          |            45            |               300               |

We should see a report message on the canbus showing everything is ok.

|        Message        | steering_angle_report_flags | steering_angle_report_errors |
|-----------------------|-----------------------------|------------------------------|
| STEERING_ANGLE_REPORT |           Enable            |             None             |

We can change the desired angle by sending another command.

|        Message         | steering_angle_cmd_flags | steering_angle_cmd_angle | steering_angle_cmd_max_velocity |
|------------------------|--------------------------|--------------------------|---------------------------------|
| STEERING_ANGLE_COMMAND |          Enable          |            -90           |               300               |

We should see a report message on the canbus showing everything is ok.

|        Message        | steering_angle_report_flags | steering_angle_report_errors |
|-----------------------|-----------------------------|------------------------------|
| STEERING_ANGLE_REPORT |           Enable            |             None             |

Now we send a angle request beyond the car capabilities.

|        Message         | steering_angle_cmd_flags | steering_angle_cmd_angle | steering_angle_cmd_max_velocity |
|------------------------|--------------------------|--------------------------|---------------------------------|
| STEERING_ANGLE_COMMAND |          Enable          |           9999           |               300               |

We should see a report message on the canbus showing an error, the controller
disable itself.

|        Message        | steering_angle_report_flags | steering_angle_report_errors |
|-----------------------|-----------------------------|------------------------------|
| STEERING_ANGLE_REPORT |           Disable           |       Command Rejected       |


#### Chassis Components Control (Niro only)

Take control of the wipers, turn signals, the horn, the headlamps and request the Vehicle
Identification Number (VIN) using the DriveKit API.

CHASSIS_CONTROL_COMMAND

|    Signal    |          Value Table          |
|--------------|-------------------------------|
| front_wipers | off, intermittent, slow, fast |
|  rear_wiper  |            off, on            |
| right_signal |            off, on            |
| left_signal  |            off, on            |
|     horn     |              on               |
|     vin      |              on               |
|  headlamps   |        off, low, high         |

You can send commands to one or more chassis components in a single message.

##### Example

In this example we send a command message to set the front wipers to slow mode.

| front_wiper |  rear_wiper  |  right turn signal | left turn signal |    horn    |    vin     | headlamps  |
|-------------|--------------|--------------------|------------------|------------|------------|------------|
|    slow     |  do nothing  |     do nothing     |    do nothing    | do nothing | do nothing | do nothing |

Now we send a command message to set the rear wiper and left turn signal on,
while leaving front wipers on slow.

| front_wiper |  rear_wiper  |  right turn signal | left turn signal |    horn    |    vin     | headlamps  |
|-------------|--------------|--------------------|------------------|------------|------------|------------|
| do nothing  |      on      |     do nothing     |        on        | do nothing | do nothing | do nothing |

Now we send a command to set the wipers off, while leaving the left turn signal
on.

| front_wiper |  rear_wiper  |  right turn signal | left turn signal |    horn    |    vin     | headlamps  |
|-------------|--------------|--------------------|------------------|------------|------------|------------|
|     off     |      off     |     do nothing     |    do nothing    | do nothing | do nothing | do nothing |



## Specifications

Kia Soul EV (PS) DriveKit DBC

| Item                    | Read               | Write              | Timing (ms) | Units   |
| ----------------------- | ------------------ | ------------------ | ----------- | ------- |
| Steering Angle          | :x:                | :heavy_check_mark: | 20          | Degrees |
| Steering Torque         | :x:                | :heavy_check_mark: | 20          | Degrees |
| Throttle Pedal Position | :x:                | :heavy_check_mark: | 20          | Percent |
| Brake Pedal Position    | :x:                | :heavy_check_mark: | 20          | Percent |

Kia Soul EV (PS) Vehicle DBC

| Item                    | Read               | Write | Timing (ms) | Units   |
| ----------------------- | ------------------ | ----- | ----------- | ------- |
| Steering Angle          | :heavy_check_mark: | :x:   | 20          | Degrees |
| Brake Pressure          | :heavy_check_mark: | :x:   | 20          | Bar     |
| Individual Wheel Speed  | :heavy_check_mark: | :x:   | 20          | KPH     |

Recommended max. steering velocity: 800 deg/s


Kia Soul (PS) DriveKit DBC

| Item                    | Read               | Write              | Timing (ms) | Units   |
| ----------------------- | ------------------ | ------------------ | ----------- | ------- |
| Steering Angle          | :x:                | :heavy_check_mark: | 20          | Degrees |
| Steering Torque         | :x:                | :heavy_check_mark: | 20          | Degrees |
| Throttle Pedal Position | :x:                | :heavy_check_mark: | 20          | Percent |
| Brake Pedal Position    | :x:                | :heavy_check_mark: | 20          | Percent |

Kia Soul (PS) Vehicle DBC

| Item                    | Read               | Write | Timing (ms) | Units   |
| ----------------------- | ------------------ | ----- | ----------- | ------- |
| Steering Angle          | :heavy_check_mark: | :x:   | 20          | Degrees |
| Brake Pressure          | :heavy_check_mark: | :x:   | 20          | Bar     |
| Individual Wheel Speed  | :heavy_check_mark: | :x:   | 20          | KPH     |

Recommended max. steering velocity: 800 deg/s


Kia Niro DriveKit DBC

| Item                    | Read               | Write              | Status Timing (ms) | Reaction Timing (ms) | Units   |
| ----------------------- | ------------------ | ------------------ | ------------------ | -------------------- | ------- |
| Steering Angle          | :x:                | :heavy_check_mark: | N/A                | 20                   | Degrees |
| Steering Torque         | :x:                | :heavy_check_mark: | N/A                | 20                   | Degrees |
| Throttle Pedal Position | :x:                | :heavy_check_mark: | N/A                | 20                   | Percent |
| Brake Pedal Position    | :x:                | :heavy_check_mark: | N/A                | 20                   | Percent |
| Wiper Control           | :heavy_check_mark: | :heavy_check_mark: | 200                | 1000                 | N/A     |
| Horn Control            | :heavy_check_mark: | :heavy_check_mark: | 200                | 1000                 | N/A     |
| Turn Signal Control     | :heavy_check_mark: | :heavy_check_mark: | 200                | 1000                 | N/A     |
| VIN Information         | :heavy_check_mark: | :heavy_check_mark: | 200                | 1000                 | N/A     |
| Headlamps Control       | :heavy_check_mark: | :heavy_check_mark: | 200                | 1000                 | N/A     |

Kia Niro Vehicle DBC

| Item                    | Read               | Write | Timing (ms) | Units   |
| ----------------------- | ------------------ | ----- | ----------- | ------- |
| Steering Angle          | :heavy_check_mark: | :x:   | 20          | Degrees |
| Brake Pressure          | :heavy_check_mark: | :x:   | 20          | Bar     |
| Individual Wheel Speed  | :heavy_check_mark: | :x:   | 20          | KPH     |

Recommended max. steering velocity: 800 deg/s
