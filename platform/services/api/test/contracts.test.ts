import test from "node:test";
import assert from "node:assert/strict";
test("phone numbers use E.164 format", () => {
  const valid = /^\+[1-9]\d{7,14}$/;
  assert.equal(valid.test("+421900123456"), true);
  assert.equal(valid.test("0900123456"), false);
});
test("message limits are explicit", () => {
  const maxBodyLength = 50000;
  assert.equal("a".repeat(maxBodyLength).length <= maxBodyLength, true);
  assert.equal("a".repeat(maxBodyLength + 1).length <= maxBodyLength, false);
});