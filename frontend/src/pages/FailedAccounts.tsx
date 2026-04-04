import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  Modal,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { TableColumnsType } from 'antd'
import {
  BugOutlined,
  CopyOutlined,
  DownloadOutlined,
  MailOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SearchOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { apiFetch } from '@/lib/utils'
import { TaskLogPanel } from '@/components/TaskLogPanel'

const { Text, Paragraph } = Typography

interface TaskLogItem {
  id: number
  created_at: string
  platform: string
  email: string
  status: 'success' | 'failed'
  error: string
  detail_json?: string
}

interface TaskLogListResponse {
  total: number
  items: TaskLogItem[]
}

interface RetryResponse {
  ok: boolean
  task_id: string
}

const PLATFORM_OPTIONS = [
  { value: '', label: '全部平台' },
  { value: 'chatgpt', label: 'ChatGPT' },
  { value: 'grok', label: 'Grok' },
  { value: 'kiro', label: 'Kiro' },
  { value: 'openblocklabs', label: 'OpenBlockLabs' },
  { value: 'tavily', label: 'Tavily' },
  { value: 'trae', label: 'Trae' },
  { value: 'cursor', label: 'Cursor' },
]

function formatTime(value: string) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN')
}

function summarizeError(text: string) {
  const raw = String(text || '').trim()
  if (!raw) return '未记录错误信息'
  if (raw.includes('add_phone')) return '命中 add_phone / 手机号验证'
  if (raw.includes('about_you')) return 'about_you 提交失败'
  if (raw.includes('workspace')) return 'workspace / callback 恢复失败'
  if (raw.includes('验证码') || raw.toLowerCase().includes('otp')) return '验证码阶段失败'
  if (raw.toLowerCase().includes('proxy')) return '代理异常'
  return '其他失败'
}

function parseDetail(raw?: string) {
  try {
    return JSON.parse(raw || '{}') as Record<string, any>
  } catch {
    return {}
  }
}

function toImportRecord(item: TaskLogItem) {
  const detail = parseDetail(item.detail_json)
  const extra = (detail.extra || {}) as Record<string, any>
  return {
    email: String(detail.email || item.email || '').trim(),
    password: String(detail.password || '').trim(),
    clientId: String(
      extra.client_id || extra.clientId || extra.clientID || ''
    ).trim(),
    refreshToken: String(
      extra.refresh_token || extra.refreshToken || ''
    ).trim(),
  }
}

function toImportLine(item: TaskLogItem) {
  const record = toImportRecord(item)
  const parts = [record.email, record.password]
  if (record.clientId || record.refreshToken) {
    parts.push(record.clientId, record.refreshToken)
  }
  return parts.join('----')
}

function canExportAsImportRecord(item: TaskLogItem) {
  const record = toImportRecord(item)
  return Boolean(record.email && record.password)
}

function downloadTextFile(filename: string, content: string, type = 'text/plain;charset=utf-8') {
  const blob = new Blob([content], { type })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function buildExportUrl(format: 'txt' | 'json', platform: string, ids: number[]) {
  const params = new URLSearchParams()
  params.set('format', format)
  params.set('status', 'failed')
  if (platform) params.set('platform', platform)
  if (ids.length) params.set('ids', ids.join(','))
  return `/api/tasks/logs/export?${params.toString()}`
}

async function copyText(text: string) {
  if (!text) return false
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // ignore and fallback
  }

  try {
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.setAttribute('readonly', 'true')
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    textarea.style.left = '-9999px'
    document.body.appendChild(textarea)
    textarea.focus()
    textarea.select()
    const ok = document.execCommand('copy')
    textarea.remove()
    return ok
  } catch {
    return false
  }
}

export default function FailedAccounts() {
  const [logs, setLogs] = useState<TaskLogItem[]>([])
  const [total, setTotal] = useState(0)
  const [platform, setPlatform] = useState('')
  const [keyword, setKeyword] = useState('')
  const [reasonFilter, setReasonFilter] = useState('')
  const [loading, setLoading] = useState(false)
  const [retryingId, setRetryingId] = useState<number | null>(null)
  const [activeRetryTaskId, setActiveRetryTaskId] = useState<string>('')
  const [selectedRowKeys, setSelectedRowKeys] = useState<number[]>([])
  const [batchRetrying, setBatchRetrying] = useState(false)
  const [exportPreviewOpen, setExportPreviewOpen] = useState(false)
  const [exportPreviewTitle, setExportPreviewTitle] = useState('')
  const [exportPreviewContent, setExportPreviewContent] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ page: '1', page_size: '200', status: 'failed' })
      if (platform) params.set('platform', platform)
      const data = await apiFetch(`/tasks/logs?${params}`) as TaskLogListResponse
      setLogs(data.items || [])
      setTotal(data.total || 0)
    } finally {
      setLoading(false)
    }
  }, [platform])

  useEffect(() => {
    load()
  }, [load])

  const filteredLogs = useMemo(() => {
    const q = keyword.trim().toLowerCase()
    return logs.filter((item) => {
      const summary = summarizeError(item.error)
      const matchKeyword = !q || [item.email, item.error, item.platform, summary]
        .some((value) => String(value || '').toLowerCase().includes(q))
      const matchReason = !reasonFilter || summary === reasonFilter
      return matchKeyword && matchReason
    })
  }, [logs, keyword, reasonFilter])

  useEffect(() => {
    setSelectedRowKeys((prev) => prev.filter((id) => filteredLogs.some((item) => item.id === id)))
  }, [filteredLogs])

  const uniqueEmails = useMemo(() => {
    return Array.from(new Set(filteredLogs.map((item) => item.email).filter(Boolean)))
  }, [filteredLogs])

  const topReasons = useMemo(() => {
    const counter = new Map<string, number>()
    for (const item of filteredLogs) {
      const key = summarizeError(item.error)
      counter.set(key, (counter.get(key) || 0) + 1)
    }
    return Array.from(counter.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 4)
  }, [filteredLogs])

  const selectedLogs = useMemo(
    () => filteredLogs.filter((item) => selectedRowKeys.includes(item.id)),
    [filteredLogs, selectedRowKeys],
  )

  const exportTargetLogs = selectedLogs.length > 0 ? selectedLogs : filteredLogs
  const importableLogs = exportTargetLogs.filter(canExportAsImportRecord)
  const importableIds = importableLogs.map((item) => item.id)

  const handleRetry = async (item: TaskLogItem) => {
    setRetryingId(item.id)
    try {
      const res = await apiFetch(`/tasks/logs/${item.id}/retry`, {
        method: 'POST',
      }) as RetryResponse
      setActiveRetryTaskId(res.task_id || '')
      message.success(`已创建重试任务 ${res.task_id}`)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '创建重试任务失败')
    } finally {
      setRetryingId(null)
    }
  }

  const handleCopyEmails = async () => {
    const content = uniqueEmails.join('\n')
    if (!content) {
      message.warning('当前没有可复制的失败邮箱')
      return
    }
    const ok = await copyText(content)
    if (ok) {
      message.success(`已复制 ${uniqueEmails.length} 个失败邮箱`)
    } else {
      message.error('复制失败，请尝试手动复制或切换到浏览器环境')
    }
  }

  const handleCopyDetails = async () => {
    const content = filteredLogs
      .map((item) => `${formatTime(item.created_at)}\t${item.platform}\t${item.email || '-'}\t${item.error || '-'}`)
      .join('\n')
    if (!content) {
      message.warning('当前没有可复制的失败记录')
      return
    }
    const ok = await copyText(content)
    if (ok) {
      message.success(`已复制 ${filteredLogs.length} 条失败记录`)
    } else {
      message.error('复制失败，请尝试手动复制或切换到浏览器环境')
    }
  }

  const handleExportTxt = () => {
    const lines = importableLogs
      .map(toImportLine)
      .filter((line) => line && !line.startsWith('----'))
    if (!lines.length) {
      message.warning('当前记录多为旧日志，缺少邮箱/密码字段，无法导出为导入格式')
      return
    }
    const content = lines.join('\n')
    setExportPreviewTitle('TXT 导出预览')
    setExportPreviewContent(content)
    setExportPreviewOpen(true)
    try {
      window.open(buildExportUrl('txt', platform, importableIds), '_blank', 'noopener,noreferrer')
    } catch {
      downloadTextFile('failed_accounts_import.txt', content)
    }
    message.success(`已导出 ${lines.length} 条 TXT，可直接按导入格式使用`)
  }

  const handleExportJson = () => {
    const items = importableLogs
      .map(toImportRecord)
      .filter((item) => item.email)
      .map((item) => {
        const payload: Record<string, string> = {
          email: item.email,
          password: item.password,
        }
        if (item.clientId) payload.clientId = item.clientId
        if (item.refreshToken) payload.refreshToken = item.refreshToken
        return payload
      })
    if (!items.length) {
      message.warning('当前记录多为旧日志，缺少邮箱/密码字段，无法导出为导入格式')
      return
    }
    const content = JSON.stringify(items, null, 2)
    setExportPreviewTitle('JSON 导出预览')
    setExportPreviewContent(content)
    setExportPreviewOpen(true)
    try {
      window.open(buildExportUrl('json', platform, importableIds), '_blank', 'noopener,noreferrer')
    } catch {
      downloadTextFile('failed_accounts_import.json', content, 'application/json;charset=utf-8')
    }
    message.success(`已导出 ${items.length} 条 JSON，可直接按导入格式使用`)
  }

  const handleExportFailureDetails = () => {
    const lines = exportTargetLogs.map((item) => (
      `${formatTime(item.created_at)}----${item.platform || '-'}----${item.email || '-'}----${item.error || '-'}`
    ))
    if (!lines.length) {
      message.warning('当前没有可导出的失败明细')
      return
    }
    const content = lines.join('\n')
    setExportPreviewTitle('失败明细导出预览')
    setExportPreviewContent(content)
    setExportPreviewOpen(true)
    downloadTextFile('failed_accounts_detail.txt', content)
    message.success(`已导出 ${lines.length} 条失败明细`)
  }

  const handleBatchRetry = async () => {
    if (!selectedLogs.length) {
      message.warning('请先勾选要重试的失败记录')
      return
    }
    setBatchRetrying(true)
    try {
      const results: string[] = []
      for (const item of selectedLogs) {
        const res = await apiFetch(`/tasks/logs/${item.id}/retry`, {
          method: 'POST',
        }) as RetryResponse
        if (res.task_id) results.push(res.task_id)
      }
      if (results[0]) setActiveRetryTaskId(results[0])
      message.success(`已创建 ${results.length} 个重试任务`)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '批量重试失败')
    } finally {
      setBatchRetrying(false)
    }
  }

  const columns: TableColumnsType<TaskLogItem> = [
    {
      title: '失败时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (text: string) => <Text type="secondary">{formatTime(text)}</Text>,
    },
    {
      title: '平台',
      dataIndex: 'platform',
      key: 'platform',
      width: 110,
      render: (text: string) => <Tag color="processing">{text || '-'}</Tag>,
    },
    {
      title: '邮箱',
      dataIndex: 'email',
      key: 'email',
      width: 260,
      render: (text: string) => (
        <Text copyable={Boolean(text)} style={{ fontFamily: 'monospace', fontSize: 12 }}>
          {text || '-'}
        </Text>
      ),
    },
    {
      title: '失败归类',
      key: 'reason',
      width: 220,
      render: (_, record) => <Tag color="error">{summarizeError(record.error)}</Tag>,
    },
    {
      title: '错误详情',
      dataIndex: 'error',
      key: 'error',
      render: (text: string) => (
        <Paragraph
          style={{ marginBottom: 0, whiteSpace: 'pre-wrap', fontSize: 12 }}
          ellipsis={{ rows: 2, expandable: true, symbol: '展开' }}
        >
          {text || '未记录错误详情'}
        </Paragraph>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 120,
      fixed: 'right',
      render: (_, record) => (
        <Button
          type="link"
          icon={<PlayCircleOutlined />}
          loading={retryingId === record.id}
          disabled={!record.email}
          onClick={() => void handleRetry(record)}
        >
          重试
        </Button>
      ),
    },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }} className="page-enter">
      <div
        style={{
          padding: 24,
          borderRadius: 20,
          background: 'linear-gradient(135deg, rgba(239,68,68,0.18) 0%, rgba(99,102,241,0.1) 100%)',
          border: '1px solid rgba(239,68,68,0.18)',
          position: 'relative',
          overflow: 'hidden',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
          <div style={{ maxWidth: 700 }}>
            <Space size={12} align="center" style={{ marginBottom: 12 }}>
              <div
                style={{
                  width: 40,
                  height: 40,
                  borderRadius: 999,
                  display: 'grid',
                  placeItems: 'center',
                  background: 'rgba(239,68,68,0.14)',
                  color: '#ef4444',
                }}
              >
                <WarningOutlined />
              </div>
              <Text strong style={{ fontSize: 15 }}>失败账号工作台</Text>
            </Space>
            <h1 style={{ margin: 0, fontSize: 28, lineHeight: 1.15 }}>集中查看注册失败记录，快速定位共性问题</h1>
            <p style={{ margin: '10px 0 0', color: '#7a8ba3', maxWidth: 620 }}>
              这里专门聚合所有失败任务，适合排查代理质量、邮箱源、about_you、add_phone、workspace 恢复等问题。
            </p>
          </div>
          <Space wrap>
            <Button icon={<CopyOutlined />} onClick={handleCopyEmails}>复制失败邮箱</Button>
            <Button icon={<BugOutlined />} onClick={handleCopyDetails}>复制失败明细</Button>
            <Button icon={<DownloadOutlined />} onClick={handleExportTxt}>导出 TXT</Button>
            <Button icon={<DownloadOutlined />} onClick={handleExportJson}>导出 JSON</Button>
            <Button icon={<DownloadOutlined />} onClick={handleExportFailureDetails}>导出失败明细</Button>
            <Button icon={<ReloadOutlined spin={loading} />} onClick={load} loading={loading}>刷新</Button>
          </Space>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16 }}>
        <Card bordered={false} className="hover-lift">
          <Statistic title="失败记录数" value={filteredLogs.length} prefix={<WarningOutlined />} />
        </Card>
        <Card bordered={false} className="hover-lift">
          <Statistic title="独立失败邮箱" value={uniqueEmails.length} prefix={<MailOutlined />} />
        </Card>
        <Card bordered={false} className="hover-lift">
          <Statistic title="数据库总失败数" value={total} prefix={<BugOutlined />} />
        </Card>
      </div>

      <Card bordered={false}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap', alignItems: 'center' }}>
          <Space wrap>
            <Select
              value={platform}
              onChange={setPlatform}
              style={{ width: 180 }}
              options={PLATFORM_OPTIONS}
            />
            <Input
              allowClear
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              prefix={<SearchOutlined />}
              placeholder="搜邮箱 / 错误关键词 / 平台"
              style={{ width: 280 }}
            />
            <Button
              type="primary"
              ghost
              icon={<PlayCircleOutlined />}
              disabled={!selectedLogs.length}
              loading={batchRetrying}
              onClick={() => void handleBatchRetry()}
            >
              批量重试
            </Button>
          </Space>
          <Text type="secondary">
            当前筛出 {filteredLogs.length} 条失败记录
            {selectedLogs.length ? `，已选 ${selectedLogs.length} 条` : ''}
          </Text>
        </div>

        {topReasons.length > 0 && (
          <div style={{ marginTop: 16, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {topReasons.map(([reason, count]) => (
              <Tag
                key={reason}
                color={reasonFilter === reason ? 'red' : 'volcano'}
                onClick={() => setReasonFilter((prev) => prev === reason ? '' : reason)}
                style={{ padding: '4px 10px', borderRadius: 999, cursor: 'pointer' }}
              >
                {reason} · {count}
              </Tag>
            ))}
            {reasonFilter ? (
              <Button type="link" size="small" onClick={() => setReasonFilter('')}>
                清除归类筛选
              </Button>
            ) : null}
          </div>
        )}
      </Card>

      <Alert
        type="info"
        showIcon
        message="当前页面基于任务失败日志展示"
        description={`兼容导入格式的 TXT/JSON 导出需要失败日志里带有邮箱和密码字段；旧日志若缺字段，请使用“导出失败明细”。当前可导入记录 ${importableLogs.length} / ${exportTargetLogs.length} 条。`}
      />

      <Card bordered={false} bodyStyle={{ padding: filteredLogs.length ? 8 : 32 }}>
        {filteredLogs.length === 0 ? (
          <Empty description="当前没有失败记录" />
        ) : (
          <Table
            rowKey="id"
            columns={columns}
            dataSource={filteredLogs}
            loading={loading}
            rowSelection={{
              selectedRowKeys,
              onChange: (keys) => setSelectedRowKeys(keys as number[]),
            }}
            scroll={{ x: 1120 }}
            pagination={{ pageSize: 20, showSizeChanger: false }}
          />
        )}
      </Card>

      {activeRetryTaskId ? (
        <Card
          bordered={false}
          title={`重试任务日志 · ${activeRetryTaskId}`}
          extra={<Button type="link" onClick={() => setActiveRetryTaskId('')}>收起</Button>}
        >
          <TaskLogPanel taskId={activeRetryTaskId} onDone={() => { void load() }} />
        </Card>
      ) : null}

      <Modal
        title={exportPreviewTitle}
        open={exportPreviewOpen}
        onCancel={() => setExportPreviewOpen(false)}
        footer={[
          <Button key="close" onClick={() => setExportPreviewOpen(false)}>
            关闭
          </Button>,
          <Button
            key="copy"
            type="primary"
            onClick={async () => {
              const ok = await copyText(exportPreviewContent)
              if (ok) {
                message.success('已复制导出内容')
              } else {
                message.error('复制失败，请手动选择内容复制')
              }
            }}
          >
            复制内容
          </Button>,
        ]}
        width={760}
      >
        <Input.TextArea
          value={exportPreviewContent}
          readOnly
          autoSize={{ minRows: 12, maxRows: 24 }}
          style={{ fontFamily: 'monospace' }}
        />
      </Modal>
    </div>
  )
}
