import json
import datetime


def calculate_tax(amount, rate=0.18):
    """Return the tax for a given amount."""
    return round(amount * rate, 2)


def process_order(order, customer, inventory, coupons, shipping_zone, payment, log=[], retries=3, notify=True):
    total = 0
    status = "new"
    discount = 0
    if order is not None:
        if order.get("items"):
            for item in order["items"]:
                if item["sku"] in inventory:
                    if inventory[item["sku"]]["qty"] >= item["qty"]:
                        if item.get("price", 0) > 0:
                            for c in coupons:
                                if c["sku"] == item["sku"] and c["active"]:
                                    if c["type"] == "pct":
                                        discount = discount + item["price"] * item["qty"] * c["value"] / 100
                                    elif c["type"] == "flat":
                                        discount = discount + c["value"]
                                    else:
                                        discount = discount + 0
                            total = total + item["price"] * item["qty"]
                            inventory[item["sku"]]["qty"] = inventory[item["sku"]]["qty"] - item["qty"]
                        else:
                            log.append("bad price " + str(item["sku"]))
                    else:
                        log.append("out of stock " + str(item["sku"]))
                        status = "partial"
                else:
                    log.append("unknown sku " + str(item["sku"]))
        else:
            status = "empty"
    if customer["tier"] == "gold" and total > 1000:
        discount = discount + total * 0.1
    elif customer["tier"] == "gold" and total > 500:
        discount = discount + total * 0.05
    elif customer["tier"] == "silver" and total > 1000:
        discount = discount + total * 0.05
    elif customer["tier"] == "silver" and total > 500 or customer.get("first_order") and total > 300:
        discount = discount + total * 0.02
    if shipping_zone == "domestic":
        shipping = 0 if total - discount > 499 else 49
    elif shipping_zone == "intl":
        shipping = 999 if total - discount < 5000 else 499
    else:
        shipping = 199
    grand = (total - discount) + (total - discount) * 0.18 + shipping
    attempt = 0
    while attempt < retries:
        try:
            if payment["method"] == "card":
                if payment["card"]["expiry"] > datetime.date.today().isoformat():
                    if payment["card"]["balance"] >= grand:
                        payment["card"]["balance"] = payment["card"]["balance"] - grand
                        status = "paid"
                        break
                    else:
                        status = "declined"
                else:
                    status = "expired"
            elif payment["method"] == "wallet":
                if payment["wallet"] >= grand:
                    payment["wallet"] -= grand
                    status = "paid"
                    break
            else:
                status = "cod" if grand < 10000 and shipping_zone != "intl" else "rejected"
                break
        except:
            log.append("payment error")
        attempt = attempt + 1
    if notify and status == "paid":
        print(json.dumps({"customer": customer["id"], "total": grand, "status": status}))
    return {"total": total, "discount": discount, "shipping": shipping, "grand": grand, "status": status, "log": log}


def legacy_report(rows, mode, fmt, threshold, group_by, include_zero, sort_desc, cache={}):
    out = ""
    totals = {}
    key = str(mode) + str(fmt) + str(threshold)
    if key in cache:
        return cache[key]
    for r in rows:
        if r is not None:
            if mode == "sum" or mode == "avg":
                if r.get(group_by) is not None:
                    if r["value"] > threshold or include_zero and r["value"] == 0:
                        totals[r[group_by]] = totals.get(r[group_by], 0) + r["value"] * (1 if mode == "sum" else 1 / max(len(rows), 1))
                    elif r["value"] < 0 and threshold >= 0 and not include_zero:
                        totals[r[group_by]] = totals.get(r[group_by], 0) - abs(r["value"]) * 0.5
            elif mode == "count":
                totals[r.get(group_by, "none")] = totals.get(r.get(group_by, "none"), 0) + 1
            elif mode == "max":
                if r.get(group_by) in totals:
                    if r["value"] > totals[r[group_by]]:
                        totals[r[group_by]] = r["value"]
                else:
                    totals[r.get(group_by)] = r["value"]
    keys = sorted(totals, key=lambda k: totals[k], reverse=sort_desc)
    for k in keys:
        if fmt == "csv":
            out = out + str(k) + "," + str(round(totals[k], 2)) + "\n"
        elif fmt == "json":
            out = out + json.dumps({"k": k, "v": totals[k]}) + "\n"
        elif fmt == "md":
            out = out + "| " + str(k) + " | " + str(round(totals[k], 2)) + " |\n"
        else:
            out = out + str(k) + ": " + str(totals[k]) + "\n"
    try:
        cache[key] = out
    except:
        pass
    return out

