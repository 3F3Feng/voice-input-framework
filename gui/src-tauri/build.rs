//! 除了 `tauri_build::build()`,这里只做一件事:给每个产物打上一个唯一的构建标识。
//!
//! 为什么需要:本地反复构建、签名、装到 /Applications 之后,光看「v2.1.0」根本认不出
//! 手里跑的是哪一次构建的产物 —— 版本号只在发版时才动,而两次发版之间可能有几十次
//! 本地构建。构建 ID 出现在启动日志、设置界面底部和 `get_build_info` 里,报问题时
//! 一句「build 3f8a1c2d」就能对上是哪个二进制。
//!
//! 取值顺序:
//!   1. 环境变量 `VIF_BUILD_ID`(`scripts/build-macos.sh` 每次构建都现生成一个 UUID
//!      塞进来,所以走脚本的构建一定是全新的 ID);
//!   2. 没给就自己造一个 UUID 形状的串。
//!
//! `rerun-if-env-changed` 是让第 1 条真正生效的关键:少了它,cargo 认为源码没变就不会
//! 重跑 build.rs,新的 `VIF_BUILD_ID` 根本进不去二进制。反过来,不设这个环境变量时
//! build.rs 不会无故重跑,`cargo build` 的增量编译不受影响 —— 这也是对的:源码没变、
//! 二进制没变,构建 ID 就不该变。

fn main() {
    println!("cargo:rerun-if-env-changed=VIF_BUILD_ID");
    println!("cargo:rerun-if-env-changed=VIF_BUILD_TIME");
    println!("cargo:rustc-env=VIF_BUILD_ID={}", build_id());
    println!("cargo:rustc-env=VIF_BUILD_TIME={}", build_time());

    tauri_build::build()
}

fn build_id() -> String {
    if let Ok(v) = std::env::var("VIF_BUILD_ID") {
        let v = v.trim();
        if !v.is_empty() {
            return v.to_string();
        }
    }

    // 自己造一个。这里不需要密码学强度的随机 —— 目的只是让两次构建能一眼分开,
    // 纳秒时间戳加进程号已经足够,所以不为此引一个 uuid 依赖。
    // 形状仍按 UUID v4 排(版本位 4、变体位 10),这样它在任何地方都能被当成 UUID 读。
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0);
    let mut state = nanos ^ ((std::process::id() as u64) << 32);
    let mut next = move || {
        state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = state;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    };
    let (a, b) = (next(), next());
    format!(
        "{:08x}-{:04x}-4{:03x}-{:04x}-{:012x}",
        a >> 32,
        (a >> 16) & 0xffff,
        a & 0x0fff,
        0x8000 | ((b >> 48) & 0x3fff),
        b & 0xffff_ffff_ffff,
    )
}

/// 构建时刻,`YYYY-MM-DD HH:MM UTC`。
///
/// 构建 ID 能区分产物,但区分不出先后;装反了版本时最先想看的就是这一行。
/// 为一个字符串引 chrono 不值当,所以就地把 Unix 秒换算成公历。
fn build_time() -> String {
    if let Ok(v) = std::env::var("VIF_BUILD_TIME") {
        let v = v.trim();
        if !v.is_empty() {
            return v.to_string();
        }
    }
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let (days, rem) = (secs.div_euclid(86_400), secs.rem_euclid(86_400));
    let (y, m, d) = civil_from_days(days);
    format!(
        "{:04}-{:02}-{:02} {:02}:{:02} UTC",
        y,
        m,
        d,
        rem / 3600,
        (rem % 3600) / 60
    )
}

/// Howard Hinnant 的 `civil_from_days`:天数(相对 1970-01-01)→ 公历年月日。
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}
