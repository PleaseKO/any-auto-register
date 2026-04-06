import { useState } from 'react'
import { Button, Card, Input, Space, Typography, message } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { BatchResultDialog, type BatchResult } from '@/components/BatchResultDialog'
import { apiFetch } from '@/lib/utils'

export default function AppleMailImportPage() {
  const navigate = useNavigate()
  const [importing, setImporting] = useState(false)
  const [content, setContent] = useState('')
  const [filename, setFilename] = useState('')
  const [poolDir, setPoolDir] = useState('mail')
  const [result, setResult] = useState<BatchResult | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)

  const handlePickFile = async () => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.json,.txt,text/plain,application/json'
    input.onchange = async () => {
      const file = input.files?.[0]
      if (!file) return
      try {
        const text = await file.text()
        setContent(text)
        setFilename(file.name)
      } catch (error: unknown) {
        const msg = error instanceof Error ? error.message : '读取文件失败'
        message.error(msg || '读取文件失败')
      }
    }
    input.click()
  }

  const handleImport = async () => {
    if (!content.trim()) {
      message.error('请选择文件或粘贴内容')
      return
    }
    setImporting(true)
    try {
      const response = await apiFetch('/config/applemail/import', {
        method: 'POST',
        body: JSON.stringify({
          content,
          filename,
          pool_dir: String(poolDir || 'mail').trim() || 'mail',
          bind_to_config: true,
        }),
      })
      setResult({
        title: 'AppleMail 导入结果',
        total: Number(response?.count || 0),
        success: Number(response?.count || 0),
        preview: content,
      })
      setDialogOpen(true)
      message.success(`导入成功，共 ${response.count} 个邮箱，已绑定 ${response.filename}`)
      navigate('/mailpool')
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : '导入失败'
      message.error(msg || '导入失败')
    } finally {
      setImporting(false)
    }
  }

  return (
    <>
      <Card
        title="导入邮箱（AppleMail）"
        extra={
          <Space size={8}>
            <Button onClick={() => navigate('/mailpool')}>返回列表</Button>
            <Button type="primary" onClick={handleImport} loading={importing}>
              确认导入
            </Button>
          </Space>
        }
      >
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <Typography.Text type="secondary">
            支持 JSON 或 TXT。TXT 每行一条，字段用 `----` / TAB / 空格分隔；必须包含 `email + client_id + refresh_token`（可选 password、mailbox）。
          </Typography.Text>

          <Space wrap style={{ width: '100%' }}>
            <Input value={poolDir} onChange={e => setPoolDir(e.target.value)} placeholder="pool_dir（默认 mail）" style={{ width: 260 }} />
            <Input value={filename} onChange={e => setFilename(e.target.value)} placeholder="可选文件名（留空自动生成 applemail_时间.json）" style={{ width: 420 }} />
            <Button icon={<InboxOutlined />} onClick={() => void handlePickFile()}>选择文件</Button>
            <Button danger onClick={() => { setContent(''); setFilename('') }}>清空</Button>
          </Space>

          <Input.TextArea
            value={content}
            onChange={e => setContent(e.target.value)}
            rows={14}
            placeholder={'[\n  {\n    "email": "demo@example.com",\n    "clientId": "xxxx",\n    "refreshToken": "xxxx",\n    "password": "可选",\n    "folder": "INBOX"\n  }\n]\n\n或 TXT:\ndemo@example.com----password----client_id----refresh_token----INBOX'}
            style={{ fontFamily: 'monospace' }}
          />
        </Space>
      </Card>
      <BatchResultDialog open={dialogOpen} result={result} onClose={() => setDialogOpen(false)} />
    </>
  )
}
