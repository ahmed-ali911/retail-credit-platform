"""Allocation Engine — pure unit tests, no database."""
from decimal import Decimal

from app.services.allocation import OutstandingInstallment, allocate

D = Decimal


def _inst(seq, late_fee, profit, principal, iid=None):
    return OutstandingInstallment(
        installment_id=iid if iid is not None else seq,
        sequence_number=seq,
        late_fee_outstanding=D(late_fee),
        profit_outstanding=D(profit),
        principal_outstanding=D(principal),
    )


def test_payment_fully_covers_one_installment():
    out = [_inst(1, "0", "30.00", "70.00"), _inst(2, "0", "30.00", "70.00")]
    result = allocate(D("100.00"), out)

    assert len(result.allocations) == 1
    a = result.allocations[0]
    assert (a.late_fee, a.profit, a.principal) == (D("0"), D("30.00"), D("70.00"))
    assert result.allocated_amount == D("100.00")
    assert result.unallocated_amount == D("0.00")


def test_payment_partially_covers_one_installment():
    out = [_inst(1, "0", "30.00", "70.00")]
    result = allocate(D("40.00"), out)

    a = result.allocations[0]
    # Late Fee -> Profit -> Principal within the installment
    assert a.profit == D("30.00")
    assert a.principal == D("10.00")
    assert result.allocated_amount == D("40.00")
    assert result.unallocated_amount == D("0.00")


def test_payment_spans_two_installments_oldest_first_beats_profit_ranking():
    # Two installments, profit 30 + principal 70 each. Pay 120.
    out = [_inst(1, "0", "30.00", "70.00"), _inst(2, "0", "30.00", "70.00")]
    result = allocate(D("120.00"), out)

    by_seq = {a.sequence_number: a for a in result.allocations}

    # Oldest installment is settled IN FULL first — including its principal —
    # before ANY money reaches installment 2's profit.
    assert by_seq[1].profit == D("30.00")
    assert by_seq[1].principal == D("70.00")          # oldest principal paid...
    assert by_seq[2].profit == D("20.00")             # ...before newer profit finishes
    assert by_seq[2].principal == D("0.00")

    # The wrong "profit-before-principal globally" model would have produced
    # inst1.principal == 60 and inst2.profit == 30. It did not.
    assert result.allocated_amount == D("120.00")


def test_late_fee_is_taken_before_profit_and_principal():
    out = [_inst(1, "5.00", "30.00", "70.00")]
    result = allocate(D("6.00"), out)
    a = result.allocations[0]
    assert a.late_fee == D("5.00")
    assert a.profit == D("1.00")
    assert a.principal == D("0.00")


def test_overpayment_leaves_unallocated_remainder():
    out = [_inst(1, "0", "10.00", "40.00")]
    result = allocate(D("100.00"), out)
    assert result.allocated_amount == D("50.00")
    assert result.unallocated_amount == D("50.00")
