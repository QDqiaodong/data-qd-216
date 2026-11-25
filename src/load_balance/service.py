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


def process(data):
    stations=data.get("stations") if isinstance(data,dict) else None
    loads=data.get("loads") if isinstance(data,dict) else None
    envelope=data.get("envelope") if isinstance(data,dict) else None
    if not isinstance(stations,list) or not 1 <= len(stations) <= 100: raise ValueError("stations must contain 1..100 items")
    if not isinstance(loads,list) or not 1 <= len(loads) <= 1000: raise ValueError("loads must contain 1..1000 items")
    if not isinstance(envelope,list) or not 2 <= len(envelope) <= 100: raise ValueError("envelope must contain 2..100 points")
    station_map={}
    for row in stations:
        sid=row.get("id") if isinstance(row,dict) else None
        if not isinstance(sid,str) or not sid or sid in station_map: raise ValueError("station ids must be unique")
        arm=number(row.get("arm_m"),"arm_m")
        limit=number(row.get("max_weight_kg"),"max_weight_kg")
        if limit <= 0: raise ValueError("station limit must be positive")
        station_map[sid]=(arm,limit)
    ids=set(); by_station={sid:Fraction(0) for sid in station_map}; total=Fraction(0); moment=Fraction(0)
    for row in loads:
        lid=row.get("id") if isinstance(row,dict) else None
        sid=row.get("station_id") if isinstance(row,dict) else None
        if not isinstance(lid,str) or not lid or lid in ids: raise ValueError("load ids must be unique")
        if sid not in station_map: raise ValueError("unknown station")
        weight=number(row.get("weight_kg"),"weight_kg")
        if weight <= 0: raise ValueError("load weight must be positive")
        ids.add(lid); by_station[sid]+=weight; total+=weight; moment+=weight*station_map[sid][0]
    points=[]; previous=None
    for row in envelope:
        weight=number(row.get("weight_kg") if isinstance(row,dict) else None,"envelope weight")
        forward=number(row.get("forward_cg_m") if isinstance(row,dict) else None,"forward_cg_m")
        aft=number(row.get("aft_cg_m") if isinstance(row,dict) else None,"aft_cg_m")
        if forward > aft or (previous is not None and weight <= previous): raise ValueError("invalid envelope order")
        previous=weight; points.append((weight,forward,aft))
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
