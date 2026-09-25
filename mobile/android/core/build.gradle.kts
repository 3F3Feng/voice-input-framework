import org.jetbrains.kotlin.gradle.dsl.JvmTarget

// 与 Android 无关的部分:协议、重发、音量。纯 JVM,不装 Android SDK 也能 `gradle :core:test`。
plugins {
    kotlin("jvm")
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

kotlin {
    compilerOptions { jvmTarget.set(JvmTarget.JVM_17) }
}

dependencies {
    api("com.squareup.okhttp3:okhttp:5.5.0")
    // Android 自带 org.json;打进 APK 会和系统类冲突,所以只编译期依赖,测试时再带上。
    compileOnly("org.json:json:20250517")

    testImplementation("org.json:json:20250517")
    testImplementation("junit:junit:4.13.2")
    testImplementation("com.squareup.okhttp3:mockwebserver3:5.5.0")
}

tasks.test {
    // 对真实 STT 服务跑的集成测试(ServerIntegrationTest)默认跳过,设了 VIF_TEST_SERVER 才跑,
    // 见 mobile/tools/fake_stt_server.py。
    testLogging { events("failed"); exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL }
}
