# Core

Contains framework-independent primitives and policies shared by domain
modules. Infrastructure dependencies are not permitted here. Sprint 2A defines
the foundational entities, UTC validation, replaceable ID generation,
repository protocols, and the unit-of-work port.

Python 3.12 does not provide stable UUIDv7 generation, so the default adapter is
UUIDv4 behind `IdGenerator`.
