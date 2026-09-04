use std::{
    io::{Read, Write},
    net::{Ipv4Addr, SocketAddrV4, TcpListener, TcpStream},
    time::Duration,
};

use serde::Deserialize;

pub(super) const BACKEND_HOST: &str = "127.0.0.1";

pub(super) struct DesktopEndpoint {
    pub(super) port: u16,
    pub(super) token: String,
}

pub(super) fn reserve_loopback_port() -> Result<u16, String> {
    let listener = TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 0))
        .map_err(|error| format!("无法分配本地服务端口：{error}"))?;
    listener
        .local_addr()
        .map(|address| address.port())
        .map_err(|error| format!("无法读取本地服务端口：{error}"))
}

pub(super) fn generate_desktop_token() -> Result<String, String> {
    let mut bytes = [0_u8; 32];
    getrandom::fill(&mut bytes).map_err(|error| format!("无法生成桌面客户端启动凭据：{error}"))?;
    let mut token = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        use std::fmt::Write as _;
        write!(&mut token, "{byte:02x}")
            .map_err(|error| format!("无法编码桌面客户端启动凭据：{error}"))?;
    }
    Ok(token)
}

#[derive(Deserialize)]
struct HealthContract {
    status: String,
    version: String,
    runtime: String,
    desktop: bool,
}

pub(super) fn desktop_ready_is_valid(
    port: u16,
    expected_version: &str,
    desktop_token: &str,
) -> bool {
    let address = SocketAddrV4::new(Ipv4Addr::LOCALHOST, port);
    let Ok(mut stream) = TcpStream::connect_timeout(&address.into(), Duration::from_millis(300))
    else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(500)));
    let request = format!(
        "GET /api/v1/desktop/ready HTTP/1.1\r\nHost: {BACKEND_HOST}:{port}\r\nX-Risk-Agent-Desktop-Token: {desktop_token}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }

    let mut response = Vec::with_capacity(2048);
    let Ok(_) = stream.take(65_537).read_to_end(&mut response) else {
        return false;
    };
    if response.len() > 65_536 {
        return false;
    }
    let Some(header_end) = response.windows(4).position(|window| window == b"\r\n\r\n") else {
        return false;
    };
    let status_line = String::from_utf8_lossy(&response[..header_end]);
    if !(status_line.starts_with("HTTP/1.1 200") || status_line.starts_with("HTTP/1.0 200")) {
        return false;
    }
    let Ok(contract) = serde_json::from_slice::<HealthContract>(&response[header_end + 4..]) else {
        return false;
    };
    contract.status == "ok"
        && contract.runtime == "local"
        && contract.desktop
        && contract.version == expected_version
}

pub(super) fn request_desktop_shutdown(port: u16, desktop_token: &str) -> bool {
    let address = SocketAddrV4::new(Ipv4Addr::LOCALHOST, port);
    let Ok(mut stream) = TcpStream::connect_timeout(&address.into(), Duration::from_millis(500))
    else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    let request = format!(
        "POST /api/v1/desktop/shutdown HTTP/1.1\r\nHost: {BACKEND_HOST}:{port}\r\nX-Risk-Agent-Desktop-Token: {desktop_token}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = Vec::with_capacity(512);
    let Ok(_) = stream.take(4097).read_to_end(&mut response) else {
        return false;
    };
    if response.len() > 4096 || !response.windows(4).any(|window| window == b"\r\n\r\n") {
        return false;
    }
    let status_line = String::from_utf8_lossy(&response);
    status_line.starts_with("HTTP/1.1 202") || status_line.starts_with("HTTP/1.0 202")
}

#[cfg(test)]
mod tests {
    use std::thread;

    use super::*;

    #[test]
    fn reserves_an_ephemeral_loopback_port() {
        let port = reserve_loopback_port().expect("loopback port should be available");
        assert_ne!(port, 0);
    }

    #[test]
    fn desktop_ready_probe_requires_token_and_runtime_contract() {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).expect("bind fake server");
        let port = listener.local_addr().expect("fake server address").port();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept desktop ready probe");
            let mut request = [0_u8; 256];
            let length = stream
                .read(&mut request)
                .expect("read desktop ready request");
            let request = String::from_utf8_lossy(&request[..length]);
            assert!(request.starts_with("GET /api/v1/desktop/ready"));
            assert!(request.contains("X-Risk-Agent-Desktop-Token: test-token"));
            let body = r#"{"status":"ok","version":"1.2.0","runtime":"local","desktop":true}"#;
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
            .expect("write health response");
        });

        assert!(desktop_ready_is_valid(port, "1.2.0", "test-token"));
        server
            .join()
            .expect("fake desktop ready server should finish");
    }

    #[test]
    fn desktop_ready_probe_rejects_another_version_or_runtime() {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).expect("bind fake server");
        let port = listener.local_addr().expect("fake server address").port();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept desktop ready probe");
            let mut request = [0_u8; 256];
            let _ = stream
                .read(&mut request)
                .expect("read desktop ready request");
            let body = r#"{"status":"ok","version":"0.0.0","runtime":"cloud","desktop":true}"#;
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
            .expect("write health response");
        });

        assert!(!desktop_ready_is_valid(port, "1.2.0", "test-token"));
        server
            .join()
            .expect("fake desktop ready server should finish");
    }

    #[test]
    fn desktop_token_is_high_entropy_lowercase_hex() {
        let first = generate_desktop_token().expect("first token");
        let second = generate_desktop_token().expect("second token");
        assert_eq!(first.len(), 64);
        assert!(first
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)));
        assert_ne!(first, second);
    }

    #[test]
    fn graceful_shutdown_request_uses_authenticated_desktop_endpoint() {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).expect("bind fake server");
        let port = listener.local_addr().expect("fake server address").port();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept desktop shutdown request");
            let mut request = [0_u8; 512];
            let length = stream.read(&mut request).expect("read shutdown request");
            let request = String::from_utf8_lossy(&request[..length]);
            assert!(request.starts_with("POST /api/v1/desktop/shutdown"));
            assert!(request.contains("X-Risk-Agent-Desktop-Token: shutdown-token"));
            stream
                .write_all(b"HTTP/1.1 2")
                .expect("write first shutdown response fragment");
            thread::sleep(Duration::from_millis(10));
            stream
                .write_all(b"02 Accepted\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                .expect("write second shutdown response fragment");
        });

        assert!(request_desktop_shutdown(port, "shutdown-token"));
        server.join().expect("fake shutdown server should finish");
    }
}
