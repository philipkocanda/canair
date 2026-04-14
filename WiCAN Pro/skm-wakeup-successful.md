Excellent! This is very detailed. The SKM wakeup sequence is:

Extended diagnostic session: 10 03
Wait for positive response: 50 03
ACC On: 2f b1 08 03 0a 0a 05
This is from Kia Soul, so the magic bytes (0a 0a 05) might differ. Let me also note from the "ECUs awake" file — IGPM/BCM was already observed as NOT responding during AC charging. So this is expected.

Let me try the SKM wakeup carefully. First, let me see if the SKM itself responds:

$ cd ~/projects/ioniq-can-reverse-engineering/WiCAN\ Pro && python3 can-request.py --raw 7A5:1001 --wican home 2>&1

Connecting to WiCAN at 10.0.2.86...
Connected. Initializing ELM327...
Ready.

  TX: 0x7A5  Request: 1001
  Response (2 bytes): 5001

  Idx:   0  1
  Hex:  50 01

NOTE: WiCAN is now in terminal mode. AutoPID (MQTT/HA data feed) is paused.
      Run with --reboot or reboot manually to restore AutoPID.

The SKM is awake and responded with 50 01 (positive response to DiagnosticSessionControl default session). Now let me try the wakeup sequence. I'll use interactive mode since this needs multiple sequential commands:

$ cd ~/projects/ioniq-can-reverse-engineering/WiCAN\ Pro && python3 -c "
import asyncio, websockets, json, time

async def skm_wakeup():
    uri = 'ws://10.0.2.86/ws'
    async with websockets.connect(uri) as ws:
        # Enter terminal mode
        await ws.send(json.dumps({'ws_mode': 'terminal', 'terminal_type': 'elm327'}))
        await asyncio.sleep(0.5)
        # drain
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
            except:
                break

        async def send_cmd(cmd, wait=2):
            print(f'>>> {cmd}')
            await ws.send(cmd + '\r')
            await asyncio.sleep(wait)
            response = ''
            while True:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    if isinstance(msg, str):
                        try:
                            d = json.loads(msg)
                            if 'data' in d:
                                response += d['data']
                        except:
                            response += msg
                except:
                    break
            print(f'<<< {response.strip()}')
            return response

        # Set up for SKM (0x7A5)
        await send_cmd('ATSH7A5', 1)
        await send_cmd('ATFCSH7A5', 1)
        
        # Step 1: Extended diagnostic session
        print('\\n--- Step 1: Extended diagnostic session ---')
        resp = await send_cmd('1003', 2)
        
        # Step 2: ACC On command (with magic bytes 0A 0A 05)
        print('\\n--- Step 2: ACC On (2f b1 08 03 0a 0a 05) ---')
        resp = await send_cmd('2FB108030A0A05', 3)
        
        # Step 3: Try IGPM now
        print('\\n--- Step 3: Try IGPM 22BC03 ---')
        await send_cmd('ATSH770', 1)
        await send_cmd('ATFCSH770', 1)
        resp = await send_cmd('22BC03', 3)
        
        # Step 4: Try IGPM 22BC04
        print('\\n--- Step 4: Try IGPM 22BC04 ---')
        resp = await send_cmd('22BC04', 3)

        # Step 5: Try CLU
        print('\\n--- Step 5: Try CLU 22B002 ---')
        await send_cmd('ATSH7C6', 1)
        await send_cmd('ATFCSH7C6', 1)
        resp = await send_cmd('22B002', 3)

        # Step 6: Turn ACC off
        print('\\n--- Step 6: ACC Off ---')
        await send_cmd('ATSH7A5', 1)
        await send_cmd('ATFCSH7A5', 1)
        resp = await send_cmd('2FB10800', 2)

asyncio.run(skm_wakeup())
" 2>&1

>>> ATSH7A5
<<< ATSH7A5
OK

>
>>> ATFCSH7A5
<<< ATFCSH7A5
OK

>

--- Step 1: Extended diagnostic session ---
>>> 1003
<<< 1003
5003

>

--- Step 2: ACC On (2f b1 08 03 0a 0a 05) ---
>>> 2FB108030A0A05
<<< F00
2FB108030A0A05
7F2F78
6FB10803

>

--- Step 3: Try IGPM 22BC03 ---
>>> ATSH770
<<< ATSH770
OK

>
>>> ATFCSH770
<<< ATFCSH770
OK

>
>>> 22BC03
<<< 22BC03
00B
0:62BC03FDEE3C
1:7300000C00AAAA

>

--- Step 4: Try IGPM 22BC04 ---
>>> 22BC04
<<< 22BC04
00B
0:62BC04B53FF4
1:EA00000042AAAA

>

--- Step 5: Try CLU 22B002 ---
>>> ATSH7C6
<<< ATSH7C6
OK

>
>>> ATFCSH7C6
<<< ATFCSH7C6
OK

>
>>> 22B002
<<< 22B002
NO DATA

>

--- Step 6: ACC Off ---
>>> ATSH7A5
<<< ATSH7A5
OK

>
>>> ATFCSH7A5
<<< ATFCSH7A5
OK

>
>>> 2FB10800
<<< F00
2FB10800
7F2F78
6FB10800

>

**User response**: The lights are on!

Ha! The ACC On command worked — it literally turned on the car's accessory power, which includes the dashboard lights! That's the SKM wakeup in action.

And look at the results — IGPM responded!

22BC03: 62BC03FDEE3C7300000C00 — door open/seatbelt status!
22BC04: 62BC04B53FF4EA00000042 — door lock status!
CLU still didn't respond (might need IGN1, not just ACC)
The SKM IOControl 2f b1 08 03 0a 0a 05 returned 7F2F78 — that's NRC 0x78 which means "requestCorrectlyReceived-ResponsePending" followed by a positive response. The magic bytes from the Kia Soul work on the Ioniq!
