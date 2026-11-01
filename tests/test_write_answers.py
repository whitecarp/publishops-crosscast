"""What a page's own request and its answer can prove about one change. Only their shape is kept."""
import base64
import json
import unittest

from video_publisher import browser_runtime as rt

TARGET = "UgwAbCdEf123"


class FakeResponse:
    def __init__(self, status, body):
        self.status, self.body = status, body

    async def json(self):
        if self.body is None:
            raise ValueError("not JSON")
        return self.body


class FakeRequest:
    def __init__(self, url="https://studio.youtube.com/youtubei/v1/comment/perform_comment_action?key=QUERY-SECRET", body="", status=200, answer=None, method="POST"):
        self.url, self.post_data, self.method = url, body, method
        self.answered = FakeResponse(status, answer if answer is not None else {})

    async def response(self):
        return self.answered


class NamesTests(unittest.TestCase):
    def test_a_request_can_name_it_in_its_address_or_its_body(self):
        self.assertTrue(rt.names_target(FakeRequest(url=f"https://x.test/delete/{TARGET}/"), TARGET))
        self.assertTrue(rt.names_target(FakeRequest(body=json.dumps({"id": TARGET})), TARGET))

    def test_a_request_can_name_it_inside_an_encoded_token(self):
        token = base64.urlsafe_b64encode(b"\x08\x06\x12\x0c" + TARGET.encode() + b"\x18\x01").decode()
        self.assertTrue(rt.names_target(FakeRequest(body=json.dumps({"actions": [token]})), TARGET))

    def test_a_token_for_another_comment_does_not_count(self):
        token = base64.urlsafe_b64encode(b"\x08\x06\x12\x0cUgwSomeoneElse\x18\x01").decode()
        self.assertFalse(rt.names_target(FakeRequest(body=json.dumps({"actions": [token]})), TARGET))

    def test_a_body_that_cannot_be_read_falls_back_to_the_address(self):
        class Unreadable(FakeRequest):
            @property
            def post_data(self):
                raise UnicodeDecodeError("utf-8", b"", 0, 1, "binary")

            @post_data.setter
            def post_data(self, value):
                pass
        self.assertFalse(rt.names_target(Unreadable(), TARGET))


class AnswerTests(unittest.IsolatedAsyncioTestCase):
    async def answers(self, *requests):
        return await rt.write_answers(list(requests), TARGET)

    async def test_it_keeps_the_shape_and_nothing_the_request_or_answer_held(self):
        answers = await self.answers(FakeRequest(body=json.dumps({"actions": [TARGET], "words": "PRIVATE-WORDS"}), answer={"actionResults": [{"status": "STATUS_SUCCEEDED", "text": "PRIVATE-WORDS"}]}))
        self.assertEqual([{"path": "/youtubei/v1/comment/perform_comment_action", "names_target": True, "http": 200, "fields": ["actionResults"], "objected": False}], answers)
        self.assertNotIn("SECRET", json.dumps(answers))

    async def test_a_field_name_that_looks_like_someones_words_is_left_out(self):
        answers = await self.answers(FakeRequest(body=TARGET, answer={"ok": 1, "a viewer's words": 2}))
        self.assertEqual(["ok"], answers[0]["fields"])

    async def test_only_post_requests_count(self):
        self.assertEqual([], await self.answers(FakeRequest(body=TARGET, method="GET")))

    async def test_an_error_or_a_failed_status_anywhere_is_an_objection(self):
        for answer in ({"error": {"code": 409}}, {"errors": [{"message": "No"}]}, {"status": "fail"}, {"actionResults": [{"status": "STATUS_FAILED"}]}, {"a": {"b": {"status": "ACCESS_DENIED"}}}):
            with self.subTest(answer=answer):
                self.assertTrue((await self.answers(FakeRequest(body=TARGET, answer=answer)))[0]["objected"])

    async def test_a_plain_success_is_no_objection(self):
        for answer in ({}, {"status": "ok"}, {"actionResults": [{"status": "STATUS_SUCCEEDED"}]}, {"error": None}, {"errors": []}):
            with self.subTest(answer=answer):
                self.assertFalse((await self.answers(FakeRequest(body=TARGET, answer=answer)))[0]["objected"])

    async def test_a_request_with_no_answer_is_recorded_without_one(self):
        class Pending(FakeRequest):
            async def response(self):
                raise RuntimeError("gone")
        self.assertIsNone((await self.answers(Pending(body=TARGET)))[0]["http"])


class AcknowledgedTests(unittest.TestCase):
    def answer(self, **fields):
        return {"names_target": True, "http": 200, "objected": False, **fields}

    def test_it_takes_a_request_naming_the_target_answered_without_objection(self):
        self.assertTrue(rt.acknowledged([self.answer()]))
        self.assertTrue(rt.acknowledged([self.answer(), self.answer(names_target=False, http=500)]))

    def test_nothing_naming_the_target_acknowledges_nothing(self):
        self.assertFalse(rt.acknowledged([]))
        self.assertFalse(rt.acknowledged([self.answer(names_target=False)]))

    def test_one_objection_is_enough_even_if_another_request_succeeded(self):
        self.assertFalse(rt.acknowledged([self.answer(), self.answer(objected=True)]))
        self.assertFalse(rt.acknowledged([self.answer(), self.answer(http=500)]))
        self.assertFalse(rt.acknowledged([self.answer(http=None)]))


if __name__ == "__main__":
    unittest.main()
