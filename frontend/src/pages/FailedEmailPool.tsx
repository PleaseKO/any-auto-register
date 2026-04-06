import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Input, Space, Switch, Table, Tag, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { ReloadOutlined, UploadOutlined } from '@ant-design/icons'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { BatchResultDialog, type BatchResult } from '@/components/BatchResultDialog'
import { apiFetch } from '@/lib/utils'

type TaskLogItem = {
  id: number
  platform: string
  email: string
  status: string
  error: string
  detail_json: string
  created_at: string
}

type FailedEmailRow = {
  id: number
  platform: string
  email: string
  password: string
  client_id: string
  refresh_token: string
  error: string
  created_at: string
  backfill_mode: string
  backfill_source: string
}

function safeJson(value: string): any {
  try {
    return JSON.parse(value || '{}')
  } catch {
    return {}
  }
}

function buildOutlookImportLine(row: FailedEmailRow): string | null {
  const email = String(row.email || '').trim()
  const password = String(row.password || '').trim()
  if (!email || !password) return null
  const clientId = String(row.client_id || '').trim()
  const refreshToken = String(row.refresh_token || '').trim()
  if (clientId && refreshToken) return `${email}----${password}----${clientId}----${refreshToken}`
  return `${email}----${password}`
}

export default function FailedEmailPool() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [loading, setLoading] = useState(false)
  const [rows, setRows] = useState<FailedEmailRow[]>([])
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])
  const [search, setSearch] = useState(() => String(searchParams.get('q') || ''))
  const [platform, setPlatform] = useState('')
  const [pageSize, setPageSize] = useState(200)
  const [dedupeByEmail, setDedupeByEmail] = useState(true)
  const [onlyImportable, setOnlyImportable] = useState(false)
  const [onlyBackfilled, setOnlyBackfilled] = useState(false)
  const [result, setResult] = useState<BatchResult | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const res = await apiFetch(`/tasks/logs?status=failed&page=1&page_size=${pageSize}`)
      const items: TaskLogItem[] = res?.items || []
      const mapped: FailedEmailRow[] = items.map(item => {
        const detail = safeJson(item.detail_json)
        const extra = (detail?.extra || {}) as any
        const client_id = String(extra.client_id || extra.clientId || extra.clientID || '').trim()
        const refresh_token = String(extra.refresh_token || extra.refreshToken || '').trim()
        return {
          id: Number(item.id),
          platform: String(item.platform || detail?.platform || ''),
          email: String(detail?.email || item.email || '').trim(),
          password: String(detail?.password || '').trim(),
          client_id,
          refresh_token,
          error: String(item.error || detail?.error || '').trim(),
          created_at: String(item.created_at || '').trim(),
          backfill_mode: String(extra.backfill_mode || '').trim(),
          backfill_source: String(extra.backfill_source || '').trim(),
        }
      })
      setRows(mapped)
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : '加载失败邮箱失败'
      message.error(msg || '加载失败邮箱失败')
      setRows([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const nextQ = String(searchParams.get('q') || '')
    setSearch(prev => (prev === nextQ ? prev : nextQ))
  }, [searchParams])

  const filtered = useMemo(() => {
    const q = String(search || '').trim().toLowerCase()
    const p = String(platform || '').trim().toLowerCase()
    const base = rows.filter(row => {
      if (p && String(row.platform || '').toLowerCase() !== p) return false
      if (onlyImportable && !(row.email && row.password)) return false
      if (onlyBackfilled && !row.backfill_mode) return false
      if (!q) return true
      const hay = `${row.email} ${row.error} ${row.platform} ${row.backfill_source}`.toLowerCase()
      return hay.includes(q)
    })
    if (!dedupeByEmail) return base

    const map = new Map<string, FailedEmailRow>()
    for (const row of base) {
      const key = String(row.email || '').trim().toLowerCase()
      if (!key) {
        map.set(`__empty__${row.id}`, row)
        continue
      }
      const existed = map.get(key)
      if (!existed || Number(row.id) > Number(existed.id)) {
        map.set(key, row)
      }
    }
    return Array.from(map.values()).sort((a, b) => Number(b.id) - Number(a.id))
  }, [rows, search, platform, dedupeByEmail, onlyImportable, onlyBackfilled])

  const importableCount = useMemo(() => rows.filter(r => r.email && r.password).length, [rows])
  const backfilledCount = useMemo(() => rows.filter(r => r.backfill_mode).length, [rows])

  const selectedRows = useMemo(() => {
    const set = new Set(selectedRowKeys.map(String))
    return rows.filter(r => set.has(String(r.id)))
  }, [rows, selectedRowKeys])

  const handleImportToOutlook = async () => {
    if (!selectedRows.length) {
      message.warning('请先勾选要导入的失败邮箱')
      return
    }

    const uniqueByEmail = new Map<string, FailedEmailRow>()
    for (const row of selectedRows) {
      const key = String(row.email || '').trim().toLowerCase()
      if (!key) continue
      if (!uniqueByEmail.has(key)) uniqueByEmail.set(key, row)
    }

    const lines = Array.from(uniqueByEmail.values())
      .map(buildOutlookImportLine)
      .filter((line): line is string => Boolean(line))

    const skipped = selectedRows.length - lines.length
    if (!lines.length) {
      message.warning('所选记录缺少邮箱或密码，无法导入 Outlook 列表')
      return
    }

    try {
      const res = await apiFetch('/outlook/batch-import', {
        method: 'POST',
        body: JSON.stringify({ data: lines.join('\n'), enabled: true, source: 'failed_email_pool', source_tag: 'failed_reimport' }),
      })
      const nextResult: BatchResult = {
        title: '失败邮箱回流结果',
        total: selectedRows.length,
        success: Number(res?.success || 0),
        failed: Number(res?.failed || 0),
        skipped,
        errors: Array.isArray(res?.errors) ? res.errors : [],
        preview: lines.join('\n'),
      }
      setResult(nextResult)
      setDialogOpen(true)
      if (Number(res?.success || 0) <= 0) {
        const firstError = Array.isArray(res?.errors) && res.errors.length ? `：${res.errors[0]}` : ''
        message.error(`导入失败：成功 0 / 失败 ${res?.failed || 0}（跳过 ${skipped}）${firstError}`)
        return
      }
      if (Number(res?.failed || 0) > 0) {
        const firstError = Array.isArray(res?.errors) && res.errors.length ? `；首条错误：${res.errors[0]}` : ''
        message.warning(`部分导入成功：成功 ${res.success} / 失败 ${res.failed}（跳过 ${skipped}）${firstError}`)
      } else {
        message.success(`导入完成：成功 ${res.success} / 失败 ${res.failed}（跳过 ${skipped}）`)
      }
      navigate('/mailpool/outlook')
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : '导入失败'
      message.error(msg || '导入失败')
    }
  }

  const columns: ColumnsType<FailedEmailRow> = [
    { title: '平台', dataIndex: 'platform', width: 120, render: v => <Tag>{String(v || '-')}</Tag> },
    { title: '邮箱', dataIndex: 'email' },
    {
      title: '可导入',
      key: 'importable',
      width: 90,
      render: (_, row) => (row.email && row.password ? <Tag color="green">是</Tag> : <Tag>否</Tag>),
    },
    {
      title: 'OAuth',
      key: 'oauth',
      width: 90,
      render: (_, row) => (row.client_id && row.refresh_token ? <Tag color="blue">有</Tag> : <Tag>无</Tag>),
    },
    {
      title: '来源',
      key: 'source',
      width: 150,
      render: (_, row) =>
        row.backfill_mode ? (
          <Space direction="vertical" size={2}>
            <Tag color="gold">推断补录</Tag>
            {row.backfill_source ? <Typography.Text type="secondary">{row.backfill_source}</Typography.Text> : null}
          </Space>
        ) : (
          <Tag>实时记录</Tag>
        ),
    },
    { title: '错误', dataIndex: 'error', ellipsis: true },
    { title: '时间', dataIndex: 'created_at', width: 190, ellipsis: true },
  ]

  return (
    <>
    <Card
      title="失败邮箱列表"
      extra={
        <Space size={8}>
          <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
            刷新
          </Button>
          <Button onClick={() => navigate('/register?use_outlook_pool=1&outlook_failed_reimport_only=1')}>
            执行失败回流注册
          </Button>
          <Button onClick={() => navigate('/mailpool/failed/retry-over-2')}>重试&gt;2次</Button>
          <Button onClick={() => navigate('/mailpool/outlook')}>查看 Outlook 列表</Button>
          <Button
            type="primary"
            icon={<UploadOutlined />}
            onClick={() => void handleImportToOutlook()}
            disabled={!importableCount}
          >
            导入到 Outlook 列表
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" style={{ width: '100%' }} size={12}>
        <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
          <Space wrap>
            <Tag color="blue">失败记录: {rows.length} 条</Tag>
            <Tag color="green">可导入: {importableCount} 条</Tag>
            <Tag color="gold">推断补录: {backfilledCount} 条</Tag>
            <Tag color="purple">当前筛选: {filtered.length} 条</Tag>
            <Tag color="geekblue">已选择: {selectedRows.length} 条</Tag>
            <Tag color={dedupeByEmail ? 'gold' : 'default'}>邮箱去重: {dedupeByEmail ? '开启' : '关闭'}</Tag>
            <Typography.Text type="secondary">导入目标：Outlook（本地导入）</Typography.Text>
            {!importableCount ? (
              <Typography.Text type="warning">当前失败记录里没有邮箱/密码，历史记录暂时无法回流导入</Typography.Text>
            ) : null}
          </Space>
          <Space wrap>
            <Input
              value={platform}
              onChange={e => setPlatform(e.target.value)}
              placeholder="平台过滤（精确匹配，如 chatgpt）"
              style={{ width: 260 }}
            />
            <Input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="搜索邮箱 / 错误 / 平台"
              style={{ width: 300 }}
            />
            <Space size={4}>
              <Typography.Text type="secondary">按邮箱去重</Typography.Text>
              <Switch checked={dedupeByEmail} onChange={setDedupeByEmail} />
            </Space>
            <Space size={4}>
              <Typography.Text type="secondary">仅看可导入</Typography.Text>
              <Switch checked={onlyImportable} onChange={setOnlyImportable} />
            </Space>
            <Space size={4}>
              <Typography.Text type="secondary">仅看推断补录</Typography.Text>
              <Switch checked={onlyBackfilled} onChange={setOnlyBackfilled} />
            </Space>
            <Input
              value={String(pageSize)}
              onChange={e => {
                const n = Number(String(e.target.value || '').trim())
                if (!Number.isFinite(n)) return
                setPageSize(Math.min(Math.max(Math.floor(n), 50), 1000))
              }}
              placeholder="拉取数量（50~1000）"
              style={{ width: 160 }}
            />
            <Button onClick={() => void load()} loading={loading}>
              应用
            </Button>
          </Space>
        </Space>

        <Table
          rowKey={row => String(row.id)}
          columns={columns}
          dataSource={filtered}
          loading={loading}
          rowSelection={{
            selectedRowKeys,
            onChange: setSelectedRowKeys,
            preserveSelectedRowKeys: true,
          }}
          pagination={{ pageSize: 50, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
        />
      </Space>
    </Card>
    <BatchResultDialog open={dialogOpen} result={result} onClose={() => setDialogOpen(false)} />
    </>
  )
}
