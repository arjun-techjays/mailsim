"""Fidelity: the official Microsoft Graph Python SDK (msgraph-sdk) against the sandbox.

The SDK runs unmodified with its default Graph middleware pipeline (retry, redirect, URL
replacement for /me, telemetry). Only two things are swapped, exactly as a user would to
point it at the sandbox: the token (a static sandbox agent key instead of an Entra ID
token) and the base URL. The HTTP transport is an in-process ASGI transport into the app,
so no socket is opened.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import httpx
import pytest
from kiota_abstractions.authentication import (
    AccessTokenProvider,
    AllowedHostsValidator,
    BaseBearerTokenAuthenticationProvider,
)
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph import GraphRequestAdapter, GraphServiceClient
from msgraph.generated.models.body_type import BodyType
from msgraph.generated.models.email_address import EmailAddress
from msgraph.generated.models.file_attachment import FileAttachment
from msgraph.generated.models.followup_flag import FollowupFlag
from msgraph.generated.models.followup_flag_status import FollowupFlagStatus
from msgraph.generated.models.item_body import ItemBody
from msgraph.generated.models.message import Message
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.recipient import Recipient
from msgraph.generated.users.item.mail_folders.item.messages.messages_request_builder import (
    MessagesRequestBuilder as FolderMessages,
)
from msgraph.generated.users.item.messages.item.copy.copy_post_request_body import (
    CopyPostRequestBody,
)
from msgraph.generated.users.item.messages.item.create_reply.create_reply_post_request_body import (
    CreateReplyPostRequestBody,
)
from msgraph.generated.users.item.messages.item.forward.forward_post_request_body import (
    ForwardPostRequestBody,
)
from msgraph.generated.users.item.messages.item.move.move_post_request_body import (
    MovePostRequestBody,
)
from msgraph.generated.users.item.messages.item.reply.reply_post_request_body import (
    ReplyPostRequestBody,
)
from msgraph.generated.users.item.messages.messages_request_builder import MessagesRequestBuilder
from msgraph.generated.users.item.send_mail.send_mail_post_request_body import (
    SendMailPostRequestBody,
)
from msgraph.graph_request_adapter import options as graph_options
from msgraph_core import GraphClientFactory
from msgraph_core.tasks.page_iterator import PageIterator

pytestmark = [pytest.mark.db, pytest.mark.filterwarnings("ignore::DeprecationWarning")]
SANDBOX = "http://sandbox.mailsim.test/graph/v1.0"


class SandboxToken(AccessTokenProvider):
    """A static sandbox agent key in place of an Entra ID token."""

    def __init__(self, key: str) -> None:
        self.key = key

    async def get_authorization_token(self, uri: str, additional_authentication_context=None):
        return self.key

    def get_allowed_hosts_validator(self) -> AllowedHostsValidator:
        return AllowedHostsValidator(["sandbox.mailsim.test"])


def sdk(app: Any, key: str) -> GraphServiceClient:
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=SANDBOX)
    http = GraphClientFactory.create_with_default_middleware(client=http, options=graph_options)
    adapter = GraphRequestAdapter(BaseBearerTokenAuthenticationProvider(SandboxToken(key)), http)
    adapter.base_url = SANDBOX
    return GraphServiceClient(request_adapter=adapter)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def to(*addresses: str) -> list[Recipient]:
    return [Recipient(email_address=EmailAddress(address=a)) for a in addresses]


def list_config(**params: Any) -> RequestConfiguration:
    query = MessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters(**params)
    return RequestConfiguration(query_parameters=query)


@pytest.fixture()
def client(world):
    return sdk(world.app, world.key)


def test_profile_folders_and_listing(world, client):
    async def go():
        me = await client.me.get()
        assert (me.mail, me.user_principal_name) == ("finance@example.test",) * 2
        folders = (await client.me.mail_folders.get()).value
        names = {f.display_name: f for f in folders}
        assert set(names) == {"Inbox", "Sent Items", "Drafts", "Deleted Items", "Archive"}
        assert (names["Inbox"].total_item_count, names["Inbox"].unread_item_count) == (2, 2)
        inbox = await client.me.mail_folders.by_mail_folder_id("inbox").get()
        assert inbox.id == names["Inbox"].id
        kids = await client.me.mail_folders.by_mail_folder_id("inbox").child_folders.get()
        assert kids.value == []
        page = await client.me.messages.get(list_config(
            select=["subject", "from", "receivedDateTime", "hasAttachments"],
            orderby=["receivedDateTime asc"], top=1, count=True))  # fmt: skip
        assert page.odata_count == 2 and page.odata_next_link
        assert page.value[0].subject == "Invoice INV-001" and page.value[0].has_attachments
        assert page.value[0].body is None  # not selected
        seen: list[str] = []

        def collect(item: Message) -> bool:
            seen.append(item.subject)
            return True

        await PageIterator(page, client.request_adapter).iterate(collect)
        assert seen == ["Invoice INV-001", "Re: Invoice INV-001"]
        folder = await client.me.mail_folders.by_mail_folder_id("inbox").messages.get(
            RequestConfiguration(query_parameters=FolderMessages.MessagesRequestBuilderGetQueryParameters(
                filter="from/emailAddress/address eq 'vendor@example.test' and hasAttachments eq true")))  # fmt: skip
        assert [m.subject for m in folder.value] == ["Invoice INV-001"]
        via_user = await client.users.by_user_id("finance@example.test").messages.get()
        assert len(via_user.value) == 2
        assert await client.me.messages.count.get() == 2

    run(go())


def test_get_patch_attachments_and_mime(world, client):
    async def go():
        msgs = (await client.me.messages.get(list_config(filter="hasAttachments eq true"))).value
        (invoice,) = msgs
        item = client.me.messages.by_message_id(invoice.id)
        full = await item.get()
        assert full.body.content_type == BodyType.Text and "INV-001" in full.body.content
        assert full.internet_message_id == "<invoice-001@example.test>"
        assert full.is_read is False and full.conversation_id
        patched = await item.patch(Message(is_read=True, categories=["Finance"],
                                           flag=FollowupFlag(flag_status=FollowupFlagStatus.Flagged)))  # fmt: skip
        assert patched.is_read and patched.categories == ["Finance"]
        assert patched.flag.flag_status == FollowupFlagStatus.Flagged
        atts = (await item.attachments.get()).value
        assert len(atts) == 1 and isinstance(atts[0], FileAttachment)
        assert (atts[0].name, atts[0].content_type) == ("INV-001.pdf", "application/pdf")
        assert atts[0].content_bytes.startswith(b"%PDF")
        one = await item.attachments.by_attachment_id(atts[0].id).get()
        assert isinstance(one, FileAttachment) and one.size == len(one.content_bytes)
        mime = await item.content.get()
        assert b"Message-ID: <invoice-001@example.test>" in mime

    run(go())


def test_send_reply_forward_move_copy(world, client):
    async def go():
        body = SendMailPostRequestBody(message=Message(subject="sdk-send", to_recipients=to(
            "vendor@example.test"), body=ItemBody(content_type=BodyType.Text, content="hello")),
            save_to_sent_items=True)  # fmt: skip
        await client.me.send_mail.post(body)
        sent = (await client.me.mail_folders.by_mail_folder_id("sentitems").messages.get()).value
        assert [m.subject for m in sent] == ["sdk-send"]
        vendor = [m for m in world.ev.state(world.run)["messages"]
                  if m["mailbox_id"] == world.vendor]  # fmt: skip
        assert [m["subject"] for m in vendor] == ["sdk-send"]
        invoice = (await client.me.messages.get(list_config(
            filter="subject eq 'Invoice INV-001'"))).value[0]  # fmt: skip
        item = client.me.messages.by_message_id(invoice.id)
        await item.reply.post(ReplyPostRequestBody(comment="Thanks, checking."))
        await item.forward.post(ForwardPostRequestBody(comment="fyi", to_recipients=to(
            "vendor@example.test")))  # fmt: skip
        thread = (await client.me.messages.get(list_config(
            filter=f"conversationId eq '{invoice.conversation_id}'"))).value  # fmt: skip
        assert {"Re: Invoice INV-001", "Invoice INV-001"} <= {m.subject for m in thread}
        moved = await item.move.post(MovePostRequestBody(destination_id="archive"))
        archive = await client.me.mail_folders.by_mail_folder_id("archive").get()
        assert moved.id == invoice.id and moved.parent_folder_id == archive.id
        copy = await item.copy.post(CopyPostRequestBody(destination_id="inbox"))
        assert copy.id != invoice.id and copy.subject == invoice.subject
        await client.me.messages.by_message_id(copy.id).delete()
        trash = await client.me.mail_folders.by_mail_folder_id("deleteditems").messages.get()
        assert [m.id for m in trash.value] == [copy.id]
        await client.me.messages.by_message_id(copy.id).permanent_delete.post()
        trash = await client.me.mail_folders.by_mail_folder_id("deleteditems").messages.get()
        assert trash.value == []

    run(go())


def test_drafts_create_update_send(world, client):
    async def go():
        draft = await client.me.messages.post(Message(subject="draft-1", to_recipients=to(
            "vendor@example.test"), body=ItemBody(content_type=BodyType.Html,
            content="<p>first</p>")))  # fmt: skip
        assert draft.is_draft and draft.body.content_type == BodyType.Html
        updated = await client.me.messages.by_message_id(draft.id).patch(
            Message(subject="draft-2", body=ItemBody(content_type=BodyType.Text, content="second"))
        )
        assert updated.id == draft.id, "a draft keeps its Graph id across updates"
        assert (updated.subject, updated.body.content) == ("draft-2", "second")
        drafts = (await client.me.mail_folders.by_mail_folder_id("drafts").messages.get()).value
        assert [d.id for d in drafts] == [draft.id]
        await client.me.messages.by_message_id(draft.id).send.post()
        drafts = (await client.me.mail_folders.by_mail_folder_id("drafts").messages.get()).value
        sent = (await client.me.mail_folders.by_mail_folder_id("sentitems").messages.get()).value
        assert drafts == [] and [m.subject for m in sent] == ["draft-2"]
        assert sent[0].is_draft is False
        invoice = (await client.me.messages.get(list_config(
            filter="subject eq 'Invoice INV-001'"))).value[0]  # fmt: skip
        reply = await client.me.messages.by_message_id(invoice.id).create_reply.post(
            CreateReplyPostRequestBody(comment="draft reply")
        )
        assert reply.is_draft and reply.subject == "RE: Invoice INV-001"
        assert reply.conversation_id == invoice.conversation_id
        assert [r.email_address.address for r in reply.to_recipients] == ["vendor@example.test"]
        await client.me.messages.by_message_id(reply.id).send.post()
        sent = (await client.me.mail_folders.by_mail_folder_id("sentitems").messages.get(
            RequestConfiguration(query_parameters=FolderMessages.MessagesRequestBuilderGetQueryParameters(
                select=["subject", "internetMessageHeaders"],
                filter="subject eq 'RE: Invoice INV-001'")))).value  # fmt: skip
        headers = {h.name: h.value for h in sent[0].internet_message_headers}
        assert headers["In-Reply-To"] == "<invoice-001@example.test>"

    run(go())


def test_delta_and_errors(world, client):
    async def go():
        inbox = client.me.mail_folders.by_mail_folder_id("inbox").messages
        first = await inbox.delta.get()
        assert len(first.value) == 2 and first.odata_delta_link
        target = first.value[0]
        await client.me.messages.by_message_id(target.id).patch(Message(is_read=True))
        changed = await inbox.delta.with_url(first.odata_delta_link).get()
        assert [m.id for m in changed.value] == [target.id] and changed.value[0].is_read
        await client.me.messages.by_message_id(target.id).move.post(
            MovePostRequestBody(destination_id="archive")
        )
        removed = await inbox.delta.with_url(changed.odata_delta_link).get()
        assert [m.id for m in removed.value] == [target.id]
        assert removed.value[0].additional_data.get("@removed") == {"reason": "deleted"}

        with pytest.raises(ODataError) as missing:
            await client.me.messages.by_message_id("AAMkdoesnotexist").get()
        assert missing.value.response_status_code == 404
        assert missing.value.error.code == "ErrorItemNotFound"
        with pytest.raises(ODataError) as bad:
            await client.me.messages.get(list_config(filter="bodyPreview eq 'x'"))
        assert (bad.value.response_status_code, bad.value.error.code) == (400, "ErrorNotSupported")
        with pytest.raises(ODataError) as blocked:
            await client.me.send_mail.post(SendMailPostRequestBody(message=Message(
                subject="escape", to_recipients=to("someone@real-domain.example"))))  # fmt: skip
        assert blocked.value.error.code == "ErrorMessageSubmissionBlocked"
        with pytest.raises(ODataError) as other:
            await client.users.by_user_id("vendor@example.test").messages.get()
        assert (other.value.response_status_code, other.value.error.code) == (
            403, "ErrorAccessDenied")  # fmt: skip
        with pytest.raises(ODataError) as unsupported:
            await client.me.messages.by_message_id(target.id).attachments.post(
                FileAttachment(name="x.txt", content_bytes=base64.b64encode(b"x"))
            )
        assert (unsupported.value.response_status_code, unsupported.value.error.code) == (
            501, "ErrorNotSupported")  # fmt: skip
        anonymous = sdk(world.app, "msk_000000000000_" + "x" * 40)
        with pytest.raises(ODataError) as denied:  # the Graph error body parses as ODataError
            await anonymous.me.messages.get()
        assert denied.value.response_status_code == 401
        assert denied.value.error.code == "InvalidAuthenticationToken"

    run(go())
