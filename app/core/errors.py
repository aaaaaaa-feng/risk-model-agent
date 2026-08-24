from __future__ import annotations

import re


_ERROR_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")

_STATUS_CODES = {
    400: "INVALID_REQUEST",
    401: "AUTHENTICATION_REQUIRED",
    403: "OPERATION_FORBIDDEN",
    404: "ROUTE_NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "REQUEST_CONFLICT",
    413: "UPLOAD_TOO_LARGE",
    422: "REQUEST_VALIDATION_FAILED",
    429: "TOO_MANY_REQUESTS",
    500: "INTERNAL_SERVER_ERROR",
    503: "SERVICE_UNAVAILABLE",
    504: "OPERATION_TIMEOUT",
}

_MESSAGES = {
    "INVALID_REQUEST": "请求无法处理，请检查输入内容后重试。",
    "AUTHENTICATION_REQUIRED": "当前请求需要身份验证，请重新进入应用后重试。",
    "OPERATION_FORBIDDEN": "当前操作未获允许，请检查访问方式或项目状态。",
    "RESOURCE_NOT_FOUND": "请求的资源不存在或已被移除，请刷新页面后重试。",
    "ROUTE_NOT_FOUND": "请求的接口不存在，请刷新应用或检查版本是否匹配。",
    "METHOD_NOT_ALLOWED": "该接口不支持当前操作方式，请刷新应用后重试。",
    "REQUEST_CONFLICT": "当前状态不允许执行该操作，请刷新状态并按页面提示处理。",
    "REQUEST_VALIDATION_FAILED": "请求参数格式不正确，请检查必填字段、类型和长度后重试。",
    "TOO_MANY_REQUESTS": "请求过于频繁，请稍后再试。",
    "INTERNAL_SERVER_ERROR": "本地服务处理请求时发生异常，请重试；若仍失败，请查看应用日志。",
    "SERVICE_UNAVAILABLE": "本地服务暂时不可用，请稍后重试或重新启动应用。",
    "OPERATION_TIMEOUT": "操作等待超时，请重试或改用页面提供的备用方式。",
    "UPLOAD_FORMAT_UNSUPPORTED": "仅支持 CSV、XLSX、XLSM 和 XLS 文件，请重新选择文件。",
    "UPLOAD_TOO_LARGE": "文件超过当前上传上限，请调整资源设置或拆分文件后重试。",
    "UNSUPPORTED_TABLE_FORMAT": "不支持当前表格格式，请使用 CSV、XLSX、XLSM 或 XLS 文件。",
    "UNSUPPORTED_OUTPUT_FORMAT": "不支持所选输出格式，请改用 CSV 或 Excel 格式。",
    "EXCEL_SHEET_SELECTION_REQUIRED": "该 Excel 包含多个工作表，请先选择要使用的 Sheet。",
    "DATA_ASSET_READ_FAILED": "数据文件读取失败，请确认文件未损坏、编码正确，并为多 Sheet Excel 选择工作表后重试。",
    "DATA_ASSET_NOT_READY": "数据文件尚未准备完成，请先选择 Sheet 或重新导入文件。",
    "DATA_ASSET_KIND_INVALID": "数据文件类型无效，请重新选择基准表、特征表或数据字典。",
    "DATA_DICTIONARY_FIELD_COLUMN_REQUIRED": "数据字典缺少字段名列，请选择或补充字段名列后重试。",
    "EVENT_CURSOR_INVALID": "事件游标格式不正确，请刷新页面重新连接。",
    "RUN_MANIFEST_NOT_FOUND": "当前 Run 尚未生成可验证的清单，请等待任务完成或重新运行。",
    "NOTEBOOK_EXECUTION_TIMEOUT": "Notebook 单元格执行超时，请停止长时间任务、拆分单元格或适当调高超时时间后重试。",
    "TARGET_SINGLE_CLASS": "Y 的有效样本必须同时包含 0 和 1。",
    "TIME_COLUMN_REQUIRED": "时间外推切分需要可用的时间字段。",
    "NO_FEATURES_AFTER_SCREENING": "筛选后没有可入模变量，请调整可恢复规则或检查数据。",
    "NO_AVAILABLE_MODELS": "当前资源和配置下没有可运行的候选模型，请调整模型组合或资源设置。",
    "NO_SUCCESSFUL_MODELS": "候选模型均未成功完成训练，请检查数据、变量和资源设置后重试。",
    "TRAIN_CLASS_COUNT_TOO_SMALL": "训练集正负样本不足，请调整样本切分或 Y 标签有效样本。",
    "MISSING_REQUIRED_FIELDS": "评分数据缺少模型必需字段，请按模型字段契约补齐后重试。",
    "FIELD_TYPE_MISMATCH": "评分数据字段类型与模型契约不一致，请修正字段类型后重试。",
    "SCORE_INPUT_READ_FAILED": "评分文件读取失败，请确认文件格式、编码和 Excel Sheet 后重新上传。",
    "MODEL_FIELD_CONTRACT_INVALID": "模型字段契约无效，请重新导出模型包或选择其他模型版本。",
    "MODEL_SCORE_OUTPUT_INVALID": "模型评分输出无效，请检查模型包完整性后重试。",
    "SCORE_CONFIG_INVALID": "评分配置无效，请检查分数范围、方向和缩放参数。",
    "SCORE_OUTPUT_CHECKSUM_MISMATCH": "评分结果完整性校验失败，请删除本次结果并重新评分。",
    "CROSS_PROJECT_SCORING_FORBIDDEN": "评分文件与模型不属于同一项目，请在对应项目中重新选择。",
    "MODEL_ARTIFACT_CHECKSUM_MISMATCH": "模型文件完整性校验失败，请重新生成或恢复可信模型产物。",
    "ARTIFACT_FILE_MISSING": "产物文件不存在，请重新生成产物或检查项目文件夹。",
    "ARTIFACT_CHECKSUM_MISMATCH": "产物完整性校验失败，请重新生成或恢复可信产物。",
    "REPORT_READ_FAILED": "模型报告读取失败，请重新生成报告或检查项目产物是否完整。",
    "DLP_BLOCK": "安全策略阻止了可能包含原始数据、个人信息或密钥的外发请求，请移除敏感内容后重试。",
    "ARCHIVE_PASSWORD_TOO_SHORT": "迁移包密码至少需要 10 个字符。",
    "WORKSPACE_PATH_NOT_WRITABLE": "所选工作文件夹不可写，请换一个有读写权限的本机文件夹，或检查目录是否被其他程序锁定。",
    "WORKSPACE_PATH_REQUIRED": "请先选择或输入一个本机工作文件夹。",
    "WORKSPACE_PATH_TOO_BROAD": "不能把磁盘根目录或用户主目录直接设为工作文件夹，请新建一个专用文件夹后重试。",
    "WORKSPACE_PATH_NOT_DIRECTORY": "所选路径不是文件夹，请重新选择一个本机文件夹。",
    "WORKSPACE_CONFIGURED_BY_ENVIRONMENT": "工作文件夹由启动环境固定，当前页面不能更换；请移除对应环境配置并重启应用。",
    "WORKSPACE_MARKER_INVALID": "所选文件夹的工作区标记无效，请选择其他文件夹；如需恢复原项目，请先保留该目录并查看应用日志。",
    "WORKSPACE_SWITCH_ACTIVE_RUNS": "当前工作区仍有运行中的任务，请等待任务结束后再更换文件夹。",
    "WORKSPACE_SWITCH_REQUIRES_EMPTY_CURRENT_PROJECTS": "当前工作区已有项目，不能直接更换文件夹；首次设置时旧项目会保留在原目录。",
    "WORKSPACE_NATIVE_PICKER_BUSY": "系统文件夹选择器已在打开，请先完成或取消前一个窗口。",
    "WORKSPACE_NATIVE_PICKER_UNAVAILABLE": "当前系统没有可用的文件夹选择器，请直接输入完整路径。",
    "WORKSPACE_NATIVE_PICKER_TIMEOUT": "等待系统文件夹窗口超时，请重试或直接输入完整路径。",
    "WORKSPACE_NATIVE_PICKER_FAILED": "系统文件夹选择器打开失败，请重试或直接输入完整路径。",
    "PROVIDER_DISABLED": "模型 API 未启用或配置不完整，请到“设置 > 模型与 API”完成配置并测试连接。",
    "LLM_DISABLED": "LLM 已关闭：当前不会调用外部 API。可在“设置 > 模型与 API”中重新启用。",
    "PROVIDER_API_KEY_MISSING": "API 未连接：尚未配置 API 密钥。请在“设置 > 模型与 API”中填写密钥并测试连接。",
    "PROVIDER_CONFIGURATION_INCOMPLETE": "API 未连接：Provider 地址或模型配置不完整。请在“设置 > 模型与 API”中检查并测试连接。",
    "PROVIDER_AUTH_FAILED": "模型 API 身份验证失败，请检查所选配置和密钥是否正确或已过期。",
    "PROVIDER_RATE_LIMITED": "模型 API 请求过于频繁或额度受限，请稍后重试并检查账户额度。",
    "PROVIDER_HTTP_ERROR": "模型 API 返回异常状态，请检查服务地址、模型名称和服务状态后重试。",
    "PROVIDER_REQUEST_FAILED": "模型 API 连接或调用失败，请检查网络、服务地址、密钥和模型配置后重试。",
    "PROVIDER_REQUEST_INTERRUPTED": "模型 API 调用被中断，请检查网络后重试。",
    "PROVIDER_SCHEMA_INVALID": "模型 API 返回内容格式不符合要求，请检查模型兼容性或更换模型后重试。",
    "PROVIDER_EMPTY_RESPONSE": "模型 API 未返回有效内容，请重试或更换模型。",
    "PROVIDER_BUDGET_EXCEEDED": "本次模型调用超过已设置的 Token 预算，请调整预算或缩小任务后重试。",
    "PROVIDER_BASE_URL_MUST_BE_HTTPS_OR_LOCALHOST": "Provider 地址必须使用 HTTPS；本机服务可使用 localhost 或 127.0.0.1。",
    "PROVIDER_INVALID": "不支持所选 Provider，请重新选择模型 API 配置。",
    "API_FORMAT_INVALID": "API 格式无效，请选择 OpenAI 或 Anthropic 兼容格式。",
    "RUN_EXECUTION_FAILED": "当前建模节点执行失败，请查看节点提示并重试；其他 Y 任务不受影响。",
    "WORKER_EXECUTION_FAILED": "本地计算节点执行失败，请检查数据、资源设置和当前节点后重试。",
}

_PREFIX_MESSAGES = (
    ("PROVIDER_", "模型 API 配置或调用未完成，请检查密钥、地址、模型和网络后重试。"),
    ("API_", "模型 API 配置无效，请检查接口格式、地址和模型后重试。"),
    ("WORKSPACE_", "工作文件夹操作未完成，请检查目录权限与当前任务状态后重试。"),
    ("NOTEBOOK_", "Notebook 操作未完成，请检查单元格、输出文件和数据血缘后重试。"),
    ("RUN_", "建模任务当前无法执行该操作，请刷新状态并按页面提示处理。"),
    ("DECISION_", "当前确认节点无法提交，请刷新状态后重新确认。"),
    ("PROJECT_", "项目操作未完成，请检查项目状态和输入后重试。"),
    ("TARGET_", "Y 标签或样本校验未通过，请检查标签取值和有效样本。"),
    ("DATA_", "数据校验未通过，请检查文件、字段和样本后重试。"),
    ("JOIN_", "数据关联校验未通过，请检查主键、粒度和重复记录。"),
    ("CUSTOMER_", "客户主键校验未通过，请检查字段与唯一性。"),
    ("ARCHIVE_", "归档操作未完成，请检查文件、密码和当前任务状态后重试。"),
    ("BACKUP_", "备份操作未完成，请检查备份文件和当前任务状态后重试。"),
    ("SCORE_", "批量评分未完成，请检查模型版本和输入字段后重试。"),
    ("MODEL_", "模型产物操作未完成，请检查模型版本和文件完整性。"),
    ("ARTIFACT_", "产物操作未完成，请检查文件完整性后重试。"),
    ("REPORT_", "报告操作未完成，请检查 Run 状态和产物完整性。"),
    ("DLP_", "安全策略阻止了该操作，请移除敏感内容后重试。"),
    ("PII_", "检测到可能的个人信息，请移除敏感内容后重试。"),
    ("SECRET_", "检测到可能的密钥，请移除敏感内容后重试。"),
    ("SAFE_", "安全证据校验未通过，请仅提交允许的聚合信息。"),
    ("GENERATED_CODE_", "生成代码未通过安全或契约校验，请让 Agent 修复后重试。"),
    ("WORKER_", "本地计算未完成，请检查数据、资源设置和当前节点后重试。"),
)

_CONFLICT_MARKERS = (
    "BLOCK",
    "NOT_PENDING",
    "AWAITING",
    "ARCHIVED",
    "INFLATION",
    "OVERLAP",
    "CHECKSUM",
    "NOT_RECOVERABLE",
    "LOCKED",
    "WORKSPACE",
)


def normalize_error_code(value: object, fallback: str = "INVALID_REQUEST") -> str:
    """Return a bounded public code without copying arbitrary exception text."""

    try:
        fallback_code = str(fallback or "INVALID_REQUEST").strip().upper()
    except Exception:
        fallback_code = "INVALID_REQUEST"
    if not _ERROR_CODE_PATTERN.fullmatch(fallback_code):
        fallback_code = "INVALID_REQUEST"
    try:
        candidate = str(value or "").split(":", 1)[0].strip()
    except Exception:
        return fallback_code
    if _ERROR_CODE_PATTERN.fullmatch(candidate):
        return candidate
    return fallback_code


def http_error_code(status_code: int) -> str:
    return _STATUS_CODES.get(int(status_code), "HTTP_ERROR")


def public_error_message(code: object, status_code: int | None = None) -> str:
    normalized = normalize_error_code(code, http_error_code(status_code or 400))
    if normalized in _MESSAGES:
        return _MESSAGES[normalized]
    for prefix, message in _PREFIX_MESSAGES:
        if normalized.startswith(prefix):
            return message
    if status_code is not None:
        status_message = _MESSAGES.get(http_error_code(status_code))
        if status_message:
            return status_message
    return "当前操作未完成，请检查输入和页面状态后重试。"


def value_error_status(code: object) -> int:
    normalized = normalize_error_code(code)
    return 409 if any(marker in normalized for marker in _CONFLICT_MARKERS) else 400
