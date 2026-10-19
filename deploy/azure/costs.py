"""Pure, conservative credit planning; no cloud/network operation or price quote."""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING

VM = Decimal('0.0428')
IP = Decimal('0.005')
P4 = Decimal('5.279')
END = datetime(2026, 12, 19, tzinfo=timezone.utc)


def estimate(start):
    start = datetime.fromisoformat(start.replace('Z', '+00:00'))
    if start.tzinfo is None or start >= END: raise ValueError('Use an aware start before the inclusive December 18 deadline')
    hours = Decimal(str((END - start).total_seconds())) / 3600
    disk_each = P4 * hours / 730
    fixed = (VM + IP) * hours
    expected = fixed + disk_each * 2 + Decimal('.25') + 5
    # Round each prorated disk upward, then add another $1 for calendar variance.
    higher_disks = disk_each.to_integral_value(rounding=ROUND_CEILING) * 2 + 1
    higher = fixed + higher_disks + 1 + 6 + 5
    money = lambda value: str(value.quantize(Decimal('.01')))
    return {'startUtc': start.isoformat(), 'endExclusiveUtc': END.isoformat(), 'hours': str(hours),
            'expectedUsd': money(expected), 'higherUsd': money(higher),
            'reserveAfterHigherUsd': money(100 - higher),
            'expectedComponentsUsd': {'vm': money(VM * hours), 'publicIPv4': money(IP * hours),
                'eachP4': money(disk_each), 'backupAndStaging': '.25', 'egress': '0.00', 'contingency': '5.00'},
            'higherComponentsUsd': {'vmAndIp': money(fixed), 'bothP4Disks': money(higher_disks),
                'backupAndStaging': '1.00', 'chargeableEgress50GB': '6.00', 'contingency': '5.00'},
            'hardSpendingCap': False, 'meets80DollarForecast': higher <= 80}
