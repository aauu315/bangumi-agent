package com.example.bangumipythonspike

import android.os.SystemClock
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Scaffold
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import com.example.bangumipythonspike.ui.theme.BangumiPythonSpikeTheme
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        setContent {
            BangumiPythonSpikeTheme {
                var result by remember {
                    mutableStateOf("尚未查询")
                }
                var loading by remember {
                    mutableStateOf(false)
                }
                var taskJob by remember { mutableStateOf<Job?>(null) }
                var cancelRequested by remember { mutableStateOf(false) }
                var cancelTime by remember { mutableStateOf(0L) }
                var localProbe by remember { mutableStateOf(false) }
                var protocolProbe by remember { mutableStateOf(false) }
                var deepseekProbe by remember { mutableStateOf(false) }
                var deepseekStream by remember { mutableStateOf(false) }
                // S4-01：经 Anthropic 格式接口跑完整工具循环
                var agentProbe by remember { mutableStateOf(false) }
                var question by remember { mutableStateOf("葬送的芙莉莲的条目ID是多少，第一集叫什么") }
                // 仅在内存输入，不使用rememberSaveable，不写文件或日志。
                var apiKey by remember { mutableStateOf("") }
                val scope = rememberCoroutineScope()

                Scaffold(
                    modifier = Modifier.fillMaxSize()
                ) { innerPadding ->
                    Column(
                        modifier = Modifier
                            .padding(innerPadding)
                            .padding(16.dp)
                            .verticalScroll(rememberScrollState())
                    ) {
                        if (deepseekProbe) {
                            Text("DeepSeek官方接口 · deepseek-flash")
                            Text("每次开始仅发1个限长请求，会消耗API余额；只解析工具调用。")
                            OutlinedTextField(
                                value = apiKey,
                                onValueChange = { apiKey = it },
                                label = { Text("DeepSeek API Key（仅本次内存使用）") },
                                singleLine = true,
                                enabled = !loading,
                                visualTransformation = PasswordVisualTransformation(),
                                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                                modifier = Modifier.fillMaxWidth()
                            )
                            Button(
                                enabled = !loading,
                                onClick = { deepseekStream = !deepseekStream }
                            ) {
                                Text(if (deepseekStream) "当前：流式SSE（点击切换）" else "当前：非流式JSON（点击切换）")
                            }
                            Text("开始后清空输入框；不保存Key、不自动重试。取消不能保证撤销已产生的费用。")
                        }
                        if (agentProbe) {
                            Text("Agent工具循环 · DeepSeek Anthropic格式接口 · deepseek-flash")
                            Text("一个问题最多8次模型请求，会消耗API余额；只用3个公开查询工具。")
                            OutlinedTextField(
                                value = apiKey,
                                onValueChange = { apiKey = it },
                                label = { Text("DeepSeek API Key（仅本次内存使用）") },
                                singleLine = true,
                                enabled = !loading,
                                visualTransformation = PasswordVisualTransformation(),
                                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                                modifier = Modifier.fillMaxWidth()
                            )
                            OutlinedTextField(
                                value = question,
                                onValueChange = { question = it },
                                label = { Text("问题") },
                                enabled = !loading,
                                modifier = Modifier.fillMaxWidth()
                            )
                            Text("开始后清空Key输入框；不保存Key。")
                        }
                        Button(
                            enabled = !loading,
                            onClick = {
                                if (loading) return@Button
                                if ((deepseekProbe || agentProbe) && apiKey.isBlank()) {
                                    result = "请先在手机本地输入DeepSeek API Key，不要发到聊天里。"
                                    return@Button
                                }
                                val useDeepseekProbe = deepseekProbe
                                val useDeepseekStream = deepseekStream
                                val useAgentProbe = agentProbe
                                val requestQuestion = question
                                val requestKey = if (useDeepseekProbe || useAgentProbe) apiKey else ""
                                if (useDeepseekProbe || useAgentProbe) apiKey = ""
                                cancelRequested = false
                                cancelTime = 0L
                                val useLocalProbe = localProbe
                                val useProtocolProbe = protocolProbe
                                loading = true
                                result = when {
                                    useAgentProbe -> "Agent循环运行中……"
                                    useDeepseekProbe -> "正在测试DeepSeek……"
                                    useProtocolProbe -> "协议实验执行中（不联网）……"
                                    useLocalProbe -> "本地实验执行中（不联网）……"
                                    else -> "正在查询……"
                                }

                                taskJob = scope.launch {
                                    try {
                                        result = withContext(Dispatchers.IO) {
                                            ensureActive()
                                            val taskName = when {
                                                useAgentProbe -> "Agent循环实测"
                                                useDeepseekProbe -> "DeepSeek协议实验"
                                                useProtocolProbe -> "协议实验"
                                                useLocalProbe -> "本地实验"
                                                else -> "公开查询"
                                            }
                                            Log.i("PythonSpike", "$taskName：后台开始")
                                            try {
                                                // 第一段：启动 Python
                                                val alreadyStarted = Python.isStarted()
                                                val startTime = SystemClock.elapsedRealtime()

                                                if (!alreadyStarted) {
                                                    Python.start(
                                                        AndroidPlatform(applicationContext)
                                                    )
                                                }

                                                val python = Python.getInstance()
                                                val pythonReadyTime = SystemClock.elapsedRealtime()

                                                // 第二段：加载 Python 模块
                                                val module = python.getModule("spike_demo")
                                                val moduleReadyTime = SystemClock.elapsedRealtime()

                                                // 第三段：调用查询函数
                                                ensureActive()
                                                val value = if (useAgentProbe) {
                                                    python.getModule("phone_agent").callAttr(
                                                        "run_agent", requestKey, requestQuestion
                                                    ).toString()
                                                } else if (useDeepseekProbe) {
                                                    python.getModule("deepseek_probe").callAttr(
                                                        "run_deepseek_probe", requestKey, useDeepseekStream
                                                    ).toString()
                                                } else if (useProtocolProbe) {
                                                    module.callAttr("protocol_probe").toString()
                                                } else if (useLocalProbe) {
                                                    module.callAttr("cancellation_probe").toString()
                                                } else {
                                                    module.callAttr("query_subject", 253).toString()
                                                }
                                                val queryFinishedTime = SystemClock.elapsedRealtime()

                                                // 分别计算三个阶段的耗时
                                                val pythonMs = pythonReadyTime - startTime
                                                val moduleMs = moduleReadyTime - pythonReadyTime
                                                val queryMs = queryFinishedTime - moduleReadyTime

                                                val pythonInfo = if (alreadyStarted) {
                                                    "Python 已启动，本次跳过初始化"
                                                } else {
                                                    "首次 Python 初始化：${pythonMs} ms"
                                                }

                                                value + "\n\n" + pythonInfo + "\n" +
                                                    "模块加载：${moduleMs} ms\n" +
                                                    "函数执行：${queryMs} ms"
                                            } finally {
                                                Log.i("PythonSpike", "$taskName：后台结束")
                                            }
                                        }
                                    } catch (e: CancellationException) {
                                        result = if (cancelTime > 0L) {
                                            "已取消，任务已结束，结果不再展示。\n" +
                                                "点击取消后等待：${SystemClock.elapsedRealtime() - cancelTime} ms"
                                        } else {
                                            "任务已取消。"
                                        }
                                        throw e
                                    } catch (e: Exception) {
                                        result = "查询失败：${e.message}"
                                    } finally {
                                        loading = false
                                        taskJob = null
                                        cancelRequested = false
                                    }
                                }
                            }
                        ) {
                            Text(
                                if (loading) "查询中……"
                                else if (agentProbe) "开始Agent循环（会请求模型）"
                                else if (deepseekProbe) "开始DeepSeek测试（1次API请求）"
                                else if (protocolProbe) "开始协议兼容实验"
                                else if (localProbe) "开始5秒本地实验" else "查询星际牛仔"
                            )
                        }

                        Button(
                            enabled = !loading,
                            onClick = {
                                if (protocolProbe || deepseekProbe || agentProbe) {
                                    deepseekProbe = false
                                    protocolProbe = false
                                    localProbe = false
                                    agentProbe = false
                                } else {
                                    localProbe = !localProbe
                                }
                            }
                        ) {
                            Text(if (deepseekProbe || protocolProbe || localProbe || agentProbe) "切换为真实查询" else "切换为5秒本地实验（不联网）")
                        }
                        Button(
                            enabled = !loading,
                            onClick = {
                                deepseekProbe = false
                                protocolProbe = true
                                localProbe = false
                                agentProbe = false
                            }
                        ) {
                            Text("切换为协议兼容实验（不联网）")
                        }
                        Button(
                            enabled = !loading,
                            onClick = {
                                deepseekProbe = true
                                protocolProbe = false
                                localProbe = false
                                agentProbe = false
                            }
                        ) {
                            Text("切换为DeepSeek真实测试")
                        }
                        Button(
                            enabled = !loading,
                            onClick = {
                                agentProbe = true
                                deepseekProbe = false
                                protocolProbe = false
                                localProbe = false
                            }
                        ) {
                            Text("切换为Agent循环实测（S4-01）")
                        }
                        Button(
                            enabled = loading && !cancelRequested,
                            onClick = {
                                cancelRequested = true
                                cancelTime = SystemClock.elapsedRealtime()
                                result = "已请求取消，不再展示本次结果。\n正在等待底层调用结束……"
                                Log.i("PythonSpike", "用户请求取消")
                                taskJob?.cancel()
                            }
                        ) {
                            Text(if (cancelRequested) "等待后台结束……" else "取消本次任务")
                        }
                        Text(text = result)
                    }
                }
            }
        }
    }
}