# Factory module names and the profile's ECUs

Scope: mapping factory (US-market wiring) module names to the profile's ECUs, plus the EDR and
CLIMATE-CAN notes.

Factory wiring (US-market; see the caveat in `ignition-lines.md`) names the car's modules
differently from the profile. A 0x700-0x7EF address sweep
(2026-04-17, ignition on) found 30 responders, and every one has an `ecus/` file, so a factory
module with no row below either does not answer at a 0x7xx address or was not awake.

| Factory module | Profile ECU | Confidence | Basis |
|---|---|---|---|
| IGPM (gateway, power switches) | IGPM | certain | self-identifies; does not bridge private CLIMATE-CAN or H-CAN |
| SMK, DATC, ACU, AAF, CCM, VESS, AMP, AVN (MM-CAN only on the EU car), WPC, DDM, ADM, BSD LH/RH, rear camera | SKM, HVAC, SRS, AAF, CCM, VESS, AMP, AVN, WPC, DDM, ADM, BSD-L/R, RCAM | certain | same name or alias, same bus |
| EPCU (VCU + MCU + LDC) | VCU, MCU | certain | VCU identifies as "VCU with the LDC integrated" |
| IBAU (integrated brake unit) | ESC | probable | ESC's alias is AHB (active hydraulic boost); the IBAU sits on C-CAN and P-CAN |
| MDPS, EPB unit | EPS, EPB | certain | same name, C-CAN |
| Lane keeping assist unit | MFC | probable | the camera; C-CAN; only the LKAS unit is a camera |
| AEB radar / smart cruise unit | SCC | probable | the one front radar; C-CAN |
| Driver IMS (memory seat) module | PSM | probable | the car's only power-seat module; B-CAN; address next to the door modules |
| Shift button module or SCU (parking actuator control) | GSA | unresolved | both are on P-CAN; GSA is a Kostal shift assembly; which one answers is unknown |
| Cluster, BCM | CLU, BCM | certain | name, buses |
| EWP (EPCU/motor coolant pump) | EWP (0x746) | probable | Kona bench log: the pump alone answers on 0x74E and sends P-CAN 0x570; was Unknown-746 |
| ODS, DBL, charge lamp, compressor, PTC heater, the other of SBW switch / SCU | none | no responder found | not in the sweep |
| Airbag unit, second address (0x7F1) | EDR | probable | Kona bench airbag module answers on the same pair; FA00-FA0F is the spec's airbag data block |

EDR (0x7F1/0x7F9, found 2026-11-12) is probably the airbag unit's second address: it answers only
`10`, `22` and `3E`, FA02 returns its own ids, and a Kona airbag module on a bench answers on the
same pair. The April 2026 sweep reported 0x7C0 alive, but it stays silent on every CAN id in READY
and charging, so the entry was dropped as a false positive. No dash charge-lamp unit has been
found. The HVAC unit also has a private link to the compressor and PTC heater (`CLIMATE-CAN` in
`can_buses.yaml`, marked `private: true`): neither the IGPM nor the OBD-II port is on it.
The EPCU's private H-CAN, probably the MCU/VCU CCP-CAN, is likewise outside the IGPM and OBD-II;
the US EV diagram shows the BMS on P-CAN and H-CAN at the E01 multipurpose check connector.
