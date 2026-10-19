//! Temporary hosted-QA probe for PR #10. Never merge.

#[test]
fn qa_probe_fails_on_purpose() {
    assert_eq!(1 + 1, 3, "deliberate failure for hosted /test QA");
}
