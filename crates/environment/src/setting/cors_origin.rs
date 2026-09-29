use url::Url;

/// One exact or wildcard origin allowed to make cross-origin browser requests.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CorsOrigin {
    scheme: String,
    host: String,
    port: u16,
    wildcard: bool,
}

impl CorsOrigin {
    pub(crate) fn parse(value: &str) -> Option<Self> {
        let url = Url::parse(value.trim()).ok()?;
        if !matches!(url.scheme(), "http" | "https")
            || !url.username().is_empty()
            || url.password().is_some()
            || url.path() != "/"
            || url.query().is_some()
            || url.fragment().is_some()
        {
            return None;
        }

        let parsed_host = url.host_str()?;
        let (host, wildcard) = match parsed_host.strip_prefix("*.") {
            Some(host) if !host.is_empty() && !host.contains('*') => (host, true),
            Some(_) => return None,
            None if parsed_host.contains('*') => return None,
            None => (parsed_host, false),
        };

        Some(Self {
            scheme: url.scheme().to_owned(),
            host: host.to_owned(),
            port: url.port_or_known_default()?,
            wildcard,
        })
    }

    /// Matches a concrete browser origin. A wildcard covers the apex and any subdomain depth.
    pub fn matches(&self, origin: &str) -> bool {
        let Some(origin) = Self::parse(origin) else {
            return false;
        };
        if origin.wildcard || self.scheme != origin.scheme || self.port != origin.port {
            return false;
        }

        self.host == origin.host
            || self.wildcard
                && origin
                    .host
                    .strip_suffix(&self.host)
                    .is_some_and(|prefix| prefix.ends_with('.'))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_exact_origin_matches_only_the_same_origin() {
        let origin = CorsOrigin::parse("https://app.example.com").unwrap();

        assert!(origin.matches("https://app.example.com"));
        assert!(!origin.matches("https://other.example.com"));
        assert!(!origin.matches("http://app.example.com"));
    }

    #[test]
    fn a_wildcard_matches_the_apex_and_any_subdomain_depth() {
        let origin = CorsOrigin::parse("https://*.example.com").unwrap();

        assert!(origin.matches("https://example.com"));
        assert!(origin.matches("https://tenant.example.com"));
        assert!(origin.matches("https://a.b.example.com"));
    }

    #[test]
    fn a_wildcard_preserves_scheme_port_and_domain_boundaries() {
        let origin = CorsOrigin::parse("https://*.example.com:8443").unwrap();

        assert!(origin.matches("https://example.com:8443"));
        assert!(origin.matches("https://a.b.example.com:8443"));
        assert!(!origin.matches("http://tenant.example.com:8443"));
        assert!(!origin.matches("https://tenant.example.com"));
        assert!(!origin.matches("https://evil-example.com:8443"));
        assert!(!origin.matches("https://example.com.evil.test:8443"));
    }

    #[test]
    fn rejects_a_wildcard_outside_the_complete_leftmost_label() {
        for origin in [
            "*",
            "https://*example.com",
            "https://foo.*.example.com",
            "https://**.example.com",
        ] {
            assert!(CorsOrigin::parse(origin).is_none(), "{origin}");
        }
    }
}
