import { Button, List, Modal, Space, Tag, Typography, message } from 'antd'
import { CopyOutlined } from '@ant-design/icons'

const { Paragraph, Text } = Typography

export type BatchResult = {
  title: string
  total?: number
  success?: number
  failed?: number
  skipped?: number
  errors?: string[]
  description?: string
  preview?: string
}

type BatchResultDialogProps = {
  open: boolean
  onClose: () => void
  result?: BatchResult | null
  title?: string
  success?: number
  failed?: number
  skipped?: number
  errors?: string[]
  description?: string
}

async function copyText(text: string) {
  if (!text) return false
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

export function BatchResultDialog({
  open,
  onClose,
  result,
  title,
  success = 0,
  failed = 0,
  skipped = 0,
  errors = [],
  description = '',
}: BatchResultDialogProps) {
  const resolved = result || {
    title: title || '批量操作结果',
    success,
    failed,
    skipped,
    errors,
    description,
  }

  const handleCopy = async () => {
    const copyTarget = resolved.errors?.length ? resolved.errors.join('\n') : String(resolved.preview || '')
    const ok = await copyText(copyTarget)
    if (ok) message.success('错误明细已复制')
    else message.error('复制失败')
  }

  return (
    <Modal
      open={open}
      title={resolved.title}
      onCancel={onClose}
      footer={[
        <Button key="close" onClick={onClose}>
          关闭
        </Button>,
      ]}
      width={760}
    >
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        {resolved.description ? <Text type="secondary">{resolved.description}</Text> : null}
        <Space wrap>
          {typeof resolved.total === 'number' ? <Tag color="default">总数: {resolved.total}</Tag> : null}
          <Tag color="green">成功: {resolved.success || 0}</Tag>
          <Tag color="red">失败: {resolved.failed || 0}</Tag>
          <Tag color="gold">跳过: {resolved.skipped || 0}</Tag>
          <Tag color="blue">错误条数: {resolved.errors?.length || 0}</Tag>
        </Space>

        {resolved.errors?.length ? (
          <>
            <Space style={{ justifyContent: 'space-between', width: '100%' }}>
              <Text strong>错误明细</Text>
              <Button size="small" icon={<CopyOutlined />} onClick={() => void handleCopy()}>
                复制错误
              </Button>
            </Space>
            <List
              size="small"
              bordered
              dataSource={resolved.errors}
              style={{ maxHeight: 360, overflow: 'auto' }}
              renderItem={(item, index) => (
                <List.Item>
                  <Paragraph style={{ marginBottom: 0, whiteSpace: 'pre-wrap', width: '100%' }}>
                    {index + 1}. {item}
                  </Paragraph>
                </List.Item>
              )}
            />
          </>
        ) : resolved.preview ? (
          <>
            <Space style={{ justifyContent: 'space-between', width: '100%' }}>
              <Text strong>预览</Text>
              <Button size="small" icon={<CopyOutlined />} onClick={() => void handleCopy()}>
                复制预览
              </Button>
            </Space>
            <Paragraph
              style={{
                marginBottom: 0,
                whiteSpace: 'pre-wrap',
                maxHeight: 360,
                overflow: 'auto',
                padding: 12,
                border: '1px solid rgba(148,163,184,0.2)',
                borderRadius: 8,
                fontFamily: 'monospace',
              }}
            >
              {resolved.preview}
            </Paragraph>
          </>
        ) : (
          <Text type="secondary">没有错误明细。</Text>
        )}
      </Space>
    </Modal>
  )
}
