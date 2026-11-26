import pytest

from mailroom.pipeline.actions import strip_signoff

CO = "Larkspur Freight Co."


@pytest.mark.parametrize("body, expected_tail", [
    ("Hi Dana,\n\nYour total is $393.59.\n\nBest regards", "Your total is $393.59."),
    ("Hi Dana,\n\nYour total is $393.59.\n\nThanks for your patience,\nLarkspur Freight Co.", "Your total is $393.59."),
    ("Hi,\n\nNoted.\n\nWarm regards,\nThe Larkspur Freight Team\n", "Noted."),
    ("Hi,\n\nSee you then.\n\nTake care,\nLarkspur Freight Co.", "See you then."),
    ("Hi,\n\nPlease send your name.\n\nLooking forward to hearing from you,", "Please send your name."),
    # real content that merely starts with "Thanks" is kept
    ("Hi,\n\nThanks for flagging this, we will fix the invoice.", "Thanks for flagging this, we will fix the invoice."),
    ("Hi,\n\nYour shipment is in Dayton.", "Your shipment is in Dayton."),
])
def test_strip_signoff(body, expected_tail):
    assert strip_signoff(body, CO).splitlines()[-1] == expected_tail
