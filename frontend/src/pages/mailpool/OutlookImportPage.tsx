import { useMemo, useState } from 'react'
import { Button, Card, Input, Space, Table, Tag, Typography, message } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useNavigate } from 'react-router-dom'
import { BatchResultDialog, type BatchResult } from '@/components/BatchResultDialog'
import { buildCleanOutlookImportPayload, parseOutlookImportText, type OutlookImportParsedRow } from '@/lib/emailPool'
import { apiFetch } from '@/lib/utils'

export default function OutlookImportPage() {
  const navigate = useNavigate()
  const [importing, setImporting] = useState(false)
  const [value, setValue] = useState('')
  const [result, setResult] = useState<BatchResult | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)

  const parsedRows = useMemo(() => parseOutlookImportText(value), [value])
  const validRows = useMemo(() => parsedRows.filter(row => row.valid), [parsedRows])
  const invalidRows = useMemo(() => parsedRows.filter(row => !row.valid), [parsedRows])
  const duplicateRows = useMemo(() => parsedRows.filter(row => row.duplicate && row.valid), [parsedRows])
  const cleanedPayload = useMemo(() => buildCleanOutlookImportPayload(parsedRows), [parsedRows])

  const handlePickFile = async () => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.txt,text/plain'
    input.onchange = async () => {
      const file = input.files?.[0]
      if (!file) return
      try {
        setValue(await file.text())
      } catch (error: unknown) {
        const msg = error instanceof Error ? error.message : '读取文件失败'
        message.error(msg || '读取文件失败')
      }
    }
    input.click()
  }

  const handleImport = async () => {
    const payload = String(cleanedPayload || '').trim()
    if (!payload) {
      message.error('没有可导入的有效数据')
      return
    }
    setImporting(true)
    try {
      const res = await apiFetch('/outlook/batch-import', {
        method: 'POST',
        body: JSON.stringify({ data: payload, enabled: true }),
      })
      const nextResult: BatchResult = {
        title: 'Outlook 导入结果',
        total: parsedRows.length,
        success: Number(res?.success || 0),
        failed: Number(res?.failed || 0),
        skipped: invalidRows.length + Math.max(duplicateRows.length - new Set(validRows.map(row => row.email.toLowerCase())).size + duplicateRows.length, 0),
        errors: Array.isArray(res?.errors) ? res.errors : [],
        preview: payload,
      }
      setResult(nextResult)
      setDialogOpen(true)
      if (Number(res?.success || 0) > 0) {
        message.success(`导入完成：成功 ${res.success} / 失败 ${res.failed}`)
        navigate('/mailpool/outlook')
      } else {
        message.error(`导入失败：成功 0 / 失败 ${res?.failed || 0}`)
      }
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : '导入失败'
      message.error(msg || '导入失败')
    } finally {
      setImporting(false)
    }
  }

  const columns: ColumnsType<OutlookImportParsedRow> = [
    { title: '#', dataIndex: 'index', width: 70 },
    { title: '邮箱', dataIndex: 'email' },
    {
      title: '状态',
      key: 'status',
      width: 150,
      render: (_, row) => {
        if (!row.valid) return <Tag color="red">{row.reason}</Tag>
        if (row.duplicate) return <Tag color="gold">文件内重复</Tag>
        return <Tag color="green">有效</Tag>
      },
    },
    {
      title: 'OAuth',
      key: 'oauth',
      width: 90,
      render: (_, row) => (row.client_id && row.refresh_token ? <Tag color="blue">有</Tag> : <Tag>无</Tag>),
    },
  ]

  return (
    <>
      <Card
        title="导入邮箱（Outlook 本地导入）"
        extra={
          <Space size={8}>
            <Button onClick={() => navigate('/mailpool/outlook')}>返回列表</Button>
            <Button type="primary" onClick={handleImport} loading={importing}>确认导入</Button>
          </Space>
        }
      >
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <Typography.Text type="secondary">
            每行格式：`邮箱----密码` 或 `邮箱----密码----client_id----refresh_token`。导入前会自动过滤无效行，并按邮箱去重。
          </Typography.Text>
          <Space wrap>
            <Tag color="blue">总行数: {parsedRows.length}</Tag>
            <Tag color="green">有效: {validRows.length}</Tag>
            <Tag color="red">无效: {invalidRows.length}</Tag>
            <Tag color="gold">文件内重复: {duplicateRows.length}</Tag>
          </Space>
          <Space wrap>
            <Button icon={<InboxOutlined />} onClick={() => void handlePickFile()}>选择 TXT 文件</Button>
            <Button danger onClick={() => setValue('')}>清空</Button>
          </Space>
          <Input.TextArea
            value={value}
            onChange={e => setValue(e.target.value)}
            rows={12}
            placeholder={'example@outlook.com----password\nexample@outlook.com----password----client_id----refresh_token'}
            style={{ fontFamily: 'monospace' }}
          />
          <Table
            rowKey={row => `${row.index}-${row.email}-${row.raw}`}
            columns={columns}
            dataSource={parsedRows.slice(0, 100)}
            pagination={false}
            size="small"
          />
          {parsedRows.length > 100 ? <Typography.Text type="secondary">仅预览前 100 行</Typography.Text> : null}
        </Space>
      </Card>
      <BatchResultDialog open={dialogOpen} result={result} onClose={() => setDialogOpen(false)} />
    </>
  )
}
