import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

android {
    namespace = "com.phoneagent.app"
    compileSdk = 34

    // arm64 only, on purpose: the virtual-display helper and the Python runtime
// are built for it, so a Chromebook cannot run this app. Lint's ChromeOS ABI
// warning is about that gap, not about a mistake.
lint {
    disable += "ChromeOsAbiSupport"
}

defaultConfig {
        applicationId = "com.phoneagent.app"
        minSdk = 34
        targetSdk = 34
        versionCode = 8
        versionName = "0.4.4"

        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }
    // A release APK is what a person can install from a download link: the
    // debug one is signed by a per-machine key and flagged debuggable. The
    // keystore and its password live in android-app/keystore.properties,
    // which is not in the repository (see README for how to make your own).
    signingConfigs {
        create("release") {
            val props = rootProject.file("keystore.properties")
            if (props.exists()) {
                val values = Properties().apply {
                    props.inputStream().use { load(it) }
                }
                storeFile = rootProject.file(values.getProperty("storeFile", "phonego-release.jks"))
                storePassword = values.getProperty("storePassword")
                keyAlias = values.getProperty("keyAlias", "phonego")
                keyPassword = values.getProperty("keyPassword")
            }
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release")
        }
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

// `app/src/main/python/phoneagent` is a copy of ../../phoneagent, refreshed by
// sync-python.sh before each build (Chaquopy has no Kotlin-DSL source-dir API).

chaquopy {
    defaultConfig {
        // Do not hardcode a path here: whoever clones the repo has their own
        // python. local.properties (not in git) can point at one, and the
        // environment variable is the fallback.
        val localPython = rootProject.file("local.properties").let { props ->
            if (props.exists()) {
                Properties().apply { props.inputStream().use { load(it) } }
                    .getProperty("python.path")
            } else {
                null
            }
        }
        buildPython(localPython ?: System.getenv("BUILD_PYTHON") ?: "python3")
        pip {
            install("requests")
            install("PyYAML")
            install("spake2")        // IRTF SPAKE2 (pure Python) for ADB pairing
            install("cryptography")  // AES-GCM / TLS primitives
        }
    }
}

dependencies {
    // Document-start JavaScript: the only reliable moment to wrap fetch/XHR,
    // before the page's own scripts run. onPageStarted is too late for a page
    // that fetches during parse (measured on Baidu search results).
    implementation("androidx.webkit:webkit:1.11.0")
}
