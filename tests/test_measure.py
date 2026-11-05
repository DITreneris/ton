from ton_gram.measure import classify_swap, extract_costs
from ton_gram.models import Direction


def test_classify_gram_to_usdt():
    action = {
        "type": "JettonSwap",
        "status": "ok",
        "JettonSwap": {
            "dex": "stonfi",
            "ton_in": 1_000_000,
            "router": {"address": "EQCS4UEa5UaJLzOyyKieqQOQ2P9M-7kXpkO5HnP3Bv250cN3"},
            "jetton_master_out": {"address": "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"},
        },
    }
    assert classify_swap(action) == (Direction.GRAM_TO_USDT.value, 1_000_000)


def test_classify_rejects_other_pair():
    action = {
        "type": "JettonSwap",
        "status": "ok",
        "JettonSwap": {
            "dex": "stonfi",
            "amount_in": "1",
            "router": {"address": "EQCS4UEa5UaJLzOyyKieqQOQ2P9M-7kXpkO5HnP3Bv250cN3"},
            "jetton_master_in": {"address": "EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c"},
            "jetton_master_out": {"address": "EQD__________________________________________0vo"},
        },
    }
    assert classify_swap(action) is None


def test_extract_costs_attached_refund_net():
    user = "0:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    trace = {
        "transaction": {
            "utime": 100,
            "in_msg": {"decoded_op_name": "wallet_signed_v4", "value": 0, "destination": {"address": user}},
        },
        "children": [
            {
                "transaction": {
                    "utime": 100,
                    "in_msg": {"decoded_op_name": "pton_ton_transfer", "value": 311_000_000, "destination": {"address": "0:p"}},
                },
                "children": [
                    {
                        "transaction": {
                            "utime": 105,
                            "in_msg": {"decoded_op_name": "excess", "value": 272_000_000, "destination": {"address": user}},
                        },
                        "children": [],
                    }
                ],
            }
        ],
    }
    costs = extract_costs(trace, user, offer_gram_nano=1_000_000)
    assert costs["attached_gram_nano"] == 311_000_000
    assert costs["refund_gram_nano"] == 272_000_000
    assert costs["net_cost_gram_nano"] == 38_000_000
    assert costs["latency_s"] == 5
    assert costs["hops"] == 3


def test_extract_costs_usdt_to_gram_gas_only():
    user = "0:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    trace = {
        "transaction": {
            "utime": 200,
            "in_msg": {"decoded_op_name": "wallet_signed_v4", "value": 0, "destination": {"address": user}},
        },
        "children": [
            {
                "transaction": {
                    "utime": 200,
                    "in_msg": {"decoded_op_name": "jetton_transfer", "value": 300_000_000, "destination": {"address": "0:jw"}},
                },
                "children": [
                    {
                        "transaction": {
                            "utime": 201,
                            "in_msg": {"decoded_op_name": "excess", "value": 89_000_000, "destination": {"address": user}},
                        },
                        "children": [],
                    }
                ],
            }
        ],
    }
    costs = extract_costs(trace, user, offer_gram_nano=0)
    assert costs["attached_gram_nano"] == 300_000_000
    assert costs["refund_gram_nano"] == 89_000_000
    assert costs["net_cost_gram_nano"] == 211_000_000
    assert costs["latency_s"] == 1
