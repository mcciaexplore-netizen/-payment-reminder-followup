import smtplib
from unittest.mock import Mock
import pytest
from config import SendSettings
from reminders import _smtp_submit, DeliveryFailure, DeliveryUnknown


@pytest.fixture
def smtp(monkeypatch):
    client = Mock()
    client.send_message.return_value = {}
    factory = Mock(return_value=client)
    monkeypatch.setattr(smtplib, "SMTP_SSL", factory)
    monkeypatch.setattr(smtplib, "SMTP", Mock(side_effect=AssertionError("Unexpected fallback")))
    return client, factory


def submit():
    return _smtp_submit(SendSettings(sender="sender@example.com", password="test"),
                        "customer@example.com", "Subject", "Body", "<test@example.com>")


def test_smtp_submission_sets_reviewed_headers_and_message_id(smtp):
    client, factory = smtp
    submit()
    message = client.send_message.call_args.args[0]
    assert message["To"] == "customer@example.com"
    assert message["Message-ID"] == "<test@example.com>"
    assert message.get_content().strip() == "Body"
    assert factory.call_count == 1


def test_authentication_failure_never_submits(smtp):
    client, _ = smtp
    client.login.side_effect = smtplib.SMTPAuthenticationError(535, b"invalid")
    with pytest.raises(DeliveryFailure):
        submit()
    client.send_message.assert_not_called()


def test_timeout_after_submission_is_unknown_and_not_retried(smtp):
    client, factory = smtp
    client.send_message.side_effect = TimeoutError()
    with pytest.raises(DeliveryUnknown):
        submit()
    assert client.send_message.call_count == 1
    assert factory.call_count == 1


def test_connection_close_error_does_not_turn_submission_into_failure(smtp):
    client, _ = smtp
    client.close.side_effect = OSError("connection already closed")
    submit()
