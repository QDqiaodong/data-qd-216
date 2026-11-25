from fractions import Fraction


def number(value, name):
    if not isinstance(value, str) or not value or len(value) > 32:
        raise ValueError(f"{name} must be a decimal string")
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"invalid {name}") from exc


def text(value):
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def interpolate(points, weight, side):
    if weight < points[0][0] or weight > points[-1][0]:
        return None
    for left, right in zip(points, points[1:]):
        if left[0] <= weight <= right[0]:
            if weight == left[0]: return left[side]
            if weight == right[0]: return right[side]
            ratio = (weight-left[0])/(right[0]-left[0])
            return left[side] + ratio*(right[side]-left[side])
    return points[0][side]


def _parse_stations(stations):
    station_map={}
    for row in stations:
        sid=row.get("id") if isinstance(row,dict) else None
        if not isinstance(sid,str) or not sid or sid in station_map: raise ValueError("station ids must be unique")
        arm=number(row.get("arm_m"),"arm_m")
        limit=number(row.get("max_weight_kg"),"max_weight_kg")
        if limit <= 0: raise ValueError("station limit must be positive")
        station_map[sid]=(arm,limit)
    return station_map


def _parse_loads(loads, station_map):
    ids=set(); by_station={sid:Fraction(0) for sid in station_map}; total=Fraction(0); moment=Fraction(0)
    for row in loads:
        lid=row.get("id") if isinstance(row,dict) else None
        sid=row.get("station_id") if isinstance(row,dict) else None
        if not isinstance(lid,str) or not lid or lid in ids: raise ValueError("load ids must be unique")
        if sid not in station_map: raise ValueError("unknown station")
        weight=number(row.get("weight_kg"),"weight_kg")
        if weight <= 0: raise ValueError("load weight must be positive")
        ids.add(lid); by_station[sid]+=weight; total+=weight; moment+=weight*station_map[sid][0]
    return by_station,total,moment


def _parse_envelope(envelope):
    points=[]; previous=None
    for row in envelope:
        weight=number(row.get("weight_kg") if isinstance(row,dict) else None,"envelope weight")
        forward=number(row.get("forward_cg_m") if isinstance(row,dict) else None,"forward_cg_m")
        aft=number(row.get("aft_cg_m") if isinstance(row,dict) else None,"aft_cg_m")
        if forward > aft or (previous is not None and weight <= previous): raise ValueError("invalid envelope order")
        previous=weight; points.append((weight,forward,aft))
    return points


def _frame_result(station_map, points, by_station, total, moment):
    cg=moment/total
    forward=interpolate(points,total,1); aft=interpolate(points,total,2)
    station_status=[{"station_id":sid,"loaded_kg":text(weight),"limit_kg":text(station_map[sid][1]),"status":"within_limit" if weight <= station_map[sid][1] else "over_limit"} for sid,weight in sorted(by_station.items())]
    if forward is None:
        envelope_status="weight_outside_envelope"
    elif cg < forward:
        envelope_status="forward_of_limit"
    elif cg > aft:
        envelope_status="aft_of_limit"
    else:
        envelope_status="within_envelope"
    return {"total_weight_kg":text(total),"total_moment_kg_m":text(moment),"cg_m":text(cg),"forward_limit_m":None if forward is None else text(forward),"aft_limit_m":None if aft is None else text(aft),"envelope_status":envelope_status,"stations":station_status,"dispatchable":envelope_status=="within_envelope" and all(x["status"]=="within_limit" for x in station_status)}


def _single(data):
    stations=data.get("stations") if isinstance(data,dict) else None
    loads=data.get("loads") if isinstance(data,dict) else None
    envelope=data.get("envelope") if isinstance(data,dict) else None
    if not isinstance(stations,list) or not 1 <= len(stations) <= 100: raise ValueError("stations must contain 1..100 items")
    if not isinstance(loads,list) or not 1 <= len(loads) <= 1000: raise ValueError("loads must contain 1..1000 items")
    if not isinstance(envelope,list) or not 2 <= len(envelope) <= 100: raise ValueError("envelope must contain 2..100 points")
    station_map=_parse_stations(stations)
    by_station,total,moment=_parse_loads(loads,station_map)
    points=_parse_envelope(envelope)
    return _frame_result(station_map,points,by_station,total,moment)


def _quad_max(a, b, c, lo, hi):
    """Exact maximum of a*t**2 + b*t + c over the closed interval [lo, hi]."""
    candidates=[lo,hi]
    if a < 0:
        vertex=-b/(2*a)
        if lo < vertex < hi: candidates.append(vertex)
    return max(a*t*t+b*t+c for t in candidates)


def _segment_violation(station_map, points, before, after):
    """Analyse one closed inter-frame segment with exact rational arithmetic.

    Station loads vary linearly between the endpoint totals ``before``/``after``,
    so with t in [0, 1]:  w(t) = w0 + dw*t  and  m(t) = m0 + dm*t.  On every
    envelope piece the interpolated limit is linear in t, hence each centre-of-
    gravity comparison reduces to a quadratic inequality in t (exact in the
    rationals, no sampling, no extrapolation outside the envelope weight range).
    """
    over=[sid for sid,(arm,limit) in station_map.items() if before[sid] > limit or after[sid] > limit]
    w0=sum(before.values()); w1=sum(after.values())
    m0=sum(before[sid]*station_map[sid][0] for sid in before)
    m1=sum(after[sid]*station_map[sid][0] for sid in after)
    dw=w1-w0; dm=m1-m0
    low=points[0][0]; high=points[-1][0]
    weight_outside=w0 < low or w0 > high or w1 < low or w1 > high
    cuts={Fraction(0),Fraction(1)}
    if dw:
        for weight,_,_ in points:
            t=(weight-w0)/dw
            if 0 <= t <= 1: cuts.add(t)
    forward_bad=False; aft_bad=False
    ordered=sorted(cuts)
    for start,end in zip(ordered,ordered[1:]):
        wa=w0+dw*start; wb=w0+dw*end
        if min(wa,wb) < low or max(wa,wb) > high: continue  # cg is only judged inside the envelope range
        middle=(wa+wb)/2
        for (wl,fl,al),(wr,fr,ar) in zip(points,points[1:]):
            if wl <= middle <= wr:
                forward_slope=(fr-fl)/(wr-wl); aft_slope=(ar-al)/(wr-wl)
                forward_base=fl+forward_slope*(w0-wl); forward_rate=forward_slope*dw
                aft_base=al+aft_slope*(w0-wl); aft_rate=aft_slope*dw
                break
        # cg(t) >= forward(t)  <=>  w(t)*forward(t) - m(t) <= 0  (w(t) > 0)
        if _quad_max(dw*forward_rate, w0*forward_rate+dw*forward_base-dm, w0*forward_base-m0, start, end) > 0:
            forward_bad=True
        # cg(t) <= aft(t)  <=>  m(t) - w(t)*aft(t) <= 0
        if _quad_max(-dw*aft_rate, dm-w0*aft_rate-dw*aft_base, m0-w0*aft_base, start, end) > 0:
            aft_bad=True
    return weight_outside, forward_bad, aft_bad, sorted(over)


def _scenario(data):
    stations=data.get("stations")
    frames=data.get("frames")
    envelope=data.get("envelope")
    if not isinstance(stations,list) or not 1 <= len(stations) <= 100: raise ValueError("stations must contain 1..100 items")
    if not isinstance(frames,list) or not 2 <= len(frames) <= 20: raise ValueError("frames must contain 2..20 items")
    if not isinstance(envelope,list) or not 2 <= len(envelope) <= 100: raise ValueError("envelope must contain 2..100 points")
    station_map=_parse_stations(stations)
    points=_parse_envelope(envelope)
    results=[]; totals=[]
    for frame in frames:
        loads=frame.get("loads") if isinstance(frame,dict) else None
        if not isinstance(loads,list) or not 1 <= len(loads) <= 1000: raise ValueError("loads must contain 1..1000 items")
        by_station,total,moment=_parse_loads(loads,station_map)
        totals.append(by_station)
        results.append(_frame_result(station_map,points,by_station,total,moment))
    violations=[]
    for index,(before,after) in enumerate(zip(totals,totals[1:])):
        weight_outside,forward_bad,aft_bad,over=_segment_violation(station_map,points,before,after)
        reasons=[]
        if weight_outside: reasons.append("weight_outside_envelope")
        if forward_bad: reasons.append("forward_of_limit")
        if aft_bad: reasons.append("aft_of_limit")
        if reasons or over:
            violations.append({"segment_index":index,"envelope_reasons":reasons,"over_limit_station_ids":over})
    return {"eligible":not violations,"frames":results,"violations":violations}


def process(data):
    if isinstance(data,dict) and "frames" in data:
        return _scenario(data)
    return _single(data)
