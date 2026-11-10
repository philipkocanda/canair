# Glossary

This page defines the terms and abbreviations that the canair docs use. Abbreviations in the
docs also show their meaning when you point at them.

## Vehicle and protocol terms

CAN (Controller Area Network)
:   The bus that connects the electronic control units in a vehicle.

ECU (electronic control unit)
:   A computer in the vehicle, for example the battery management system. canair names each ECU
    in the `ecus/` directory of the profile.

OBD-II (On-Board Diagnostics)
:   The standard diagnostic port and protocol of a vehicle. The WiCAN plugs into this port.

UDS (Unified Diagnostic Services, ISO 14229)
:   The modern diagnostic protocol. A tester sends a request. The ECU sends a response.

KWP2000 (Keyword Protocol 2000, ISO 14230)
:   An older diagnostic protocol. Some ECUs still use it. Which ECUs use which protocol depends on
    the vehicle. See [ECU protocols](../concepts/ecu-protocols.md).

ISO-TP (ISO 15765-2)
:   The transport layer under UDS and KWP2000. It splits a long message over several 8-byte CAN
    frames.

PCI (protocol control information)
:   The ISO-TP framing bytes. They are not data. The WiCAN byte index counts them. See
    [Byte indexing](../concepts/byte-indexing.md).

SID (service identifier)
:   The first byte of a UDS or KWP2000 message. It names the service. A positive response adds
    `0x40` to the request SID.

PID (parameter identifier)
:   In canair, a diagnostic request that returns a page of data, for example `2101`. A PID
    usually holds several signals.

DID (data identifier)
:   A 2-byte UDS identifier, for example `22B002`.

LID (local identifier)
:   A 1-byte KWP2000 identifier. canair writes these as `21xx` PIDs.

RID (routine identifier)
:   A 2-byte UDS identifier that names a diagnostic routine.

NRC (negative response code)
:   The code in a refusal from an ECU. It tells you why the ECU refused the request.

DTC (diagnostic trouble code)
:   A fault code that an ECU stores. See [DTCs](../concepts/dtcs.md).

IOControl
:   The diagnostic service (UDS `0x2F`) that switches an output of an ECU, for example a light.
    It changes the state of the car. See [Safety](../concepts/safety.md).

VIN (vehicle identification number)
:   The identifier of one vehicle. It is personal data. Do not share it.

## Hardware and software terms

WiCAN
:   The WiFi OBD-II dongle that canair uses. It comes in a Pro model and a classic model.

AutoPID
:   A feature of the WiCAN that polls PIDs by itself and publishes the values. canair generates
    the AutoPID profile from your `ecus/` directory.

ELM327
:   A text-command protocol for OBD adapters. Many cheap WiFi adapters use it.

SLCAN (serial-line CAN)
:   A text protocol that carries raw CAN frames. It is the default `slcan-tcp` transport.

DBC
:   A file format that describes the signals in CAN frames. canair imports and exports it.

Transport
:   The way canair reaches the bus: `slcan-tcp`, `wican-ws` or `elm327-tcp`. See
    [How canair connects](../concepts/architecture.md).

## canair terms

Profile
:   The directory that holds everything canair knows about one vehicle. See
    [Profiles](../concepts/profiles.md).

Signal
:   A decoded quantity that canair reads from the bus, for example battery voltage. It comes from
    a diagnostic PID or from a broadcast frame.

Capture
:   A recorded payload with its context: time, label and vehicle state. Captures are the evidence
    for the signal definitions. See [Captures & states](../concepts/captures-and-states.md).

Vehicle state
:   What the car was doing when canair recorded a capture, for example `driving` or `charging`.

Variant
:   One version of a car that the profile covers, for example the battery size or the facelift.
    See [Variants](../concepts/variants.md).

Domain A and domain B
:   The two kinds of data. Domain A is request and response diagnostics. Domain B is the
    broadcast frames that the car sends by itself. See
    [Broadcast CAN frames](../concepts/broadcast-frames.md).

Verified and unverified
:   A verified signal has evidence that you confirmed against the car. An unverified signal is a
    guess that still needs proof.

Research lead
:   An open question about a PID or signal that you record in the profile. `canair research` lists
    them.

Group
:   A named set of ECU and PID selectors. Recall it as `@name`, for example `monitor @charging`.

Mirror
:   The same quantity that you can reach in two places, for example one speed that two frame IDs
    carry.
