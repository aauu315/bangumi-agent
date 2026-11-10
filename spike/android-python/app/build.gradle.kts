plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)

    id("com.chaquo.python")
}

android {
    namespace = "com.example.bangumipythonspike"
    compileSdk {
        version = release(37)
    }

    defaultConfig {
        applicationId = "com.example.bangumipythonspike"
        minSdk = 24
        targetSdk = 37
        versionCode = 1
        versionName = "1.0"

        ndk {
            abiFilters += listOf("arm64-v8a")
        }

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        release {
            optimization {
                enable = true
                packageScope = setOf("androidx.**", "kotlin.**", "kotlinx.**")
            }
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    buildFeatures {
        compose = true
    }
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    testImplementation(libs.junit)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(libs.androidx.junit)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
    debugImplementation(libs.androidx.compose.ui.tooling)
}
chaquopy {
    sourceSets {
        getByName("main") {
            // 直接读取项目原业务源码；默认手机实验目录仍保留。
            srcDir(rootProject.file("../../src"))
            // 明确打包清单，避免引入桌面MCP入口和无关文件。
            include(
                "bangumi_agent/__init__.py",
                "bangumi_agent/api_client.py",
                // S4-01：工具循环和 Anthropic 格式转换层（只用标准库）；不含依赖 mcp 的 tool_registry.py。
                "bangumi_agent/agent/__init__.py",
                "bangumi_agent/agent/messages.py",
                "bangumi_agent/agent/loop.py",
                "bangumi_agent/agent/tool_execution.py",
                "bangumi_agent/agent/anthropic_format.py",
                "spike_demo.py",
                "deepseek_probe.py",
                "phone_agent.py"
            )
        }
    }

    defaultConfig {
        version = "3.13"

        pyc {
            src = false
        }
    }
}
