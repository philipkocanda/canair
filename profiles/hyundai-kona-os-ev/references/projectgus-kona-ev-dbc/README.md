# projectgus/kona-ev-dbc — P-CAN DBC

`pcan.dbc` is copied unchanged from <https://github.com/projectgus/kona-ev-dbc> at commit
`0b569b9ce00faedf560e694f4215d6b189a4fc69`. It is MIT-licensed (see `LICENSE`, which applies to
this file and to everything derived from it), so it is not covered by the profile's CC BY 4.0
license.

`signals/P-CAN.yaml` was generated from it with `canair import dbc --bus P-CAN`. Every imported
signal carries `source: dbc:projectgus-kona-ev-dbc-pcan.dbc` and is unverified on this profile.

As published, the file lacks the `BS_:` and `BU_:` header lines, so cantools (and therefore
`canair import dbc`) refuses it. The import used a copy with those two lines added (`BU_:` listing
the transmitting nodes named in the `BO_` lines); no message or signal was changed.
