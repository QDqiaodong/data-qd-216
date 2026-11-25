# Aircraft load and balance contract

Flight-data preparers provide load stations with arm and station weight limit, positive loads assigned to stations, and an ordered gross-weight envelope whose forward/aft centre-of-gravity limits are linearly interpolated. Decimal strings use exact rational arithmetic. Total moment is the sum of weight×arm and centre of gravity is total moment/total weight.

A dispatch is allowed only when gross weight lies inside the envelope, centre of gravity lies between its interpolated limits, and every station remains within its own limit. The result preserves each station load and the distinct reason for an envelope failure. This repository does not calculate fuel burn, structural shear, take-off performance or certified aircraft limits.

The output is a structured planning data product, not an airline reservation system or operational flight release. Future fuel-tank sequencing can reuse station moments and envelope evidence.
