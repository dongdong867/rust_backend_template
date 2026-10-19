//! Temporary hosted-QA probe for PR #10. Never merge.

#[test]
fn qa_probe_outlives_the_job_timeout() {
    // Longer than the worker's 30-minute execute limit.
    std::thread::sleep(std::time::Duration::from_secs(40 * 60));
}
