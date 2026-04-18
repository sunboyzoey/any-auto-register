import { useEffect, useState } from 'react'
import {
  Card, Table, Button, Modal, Form, Input, InputNumber, Select, Switch,
  Tag, Space, Popconfirm, Row, Col, App, TimePicker, theme, Tooltip,
} from 'antd'
import PageHeader from '@/components/PageHeader'
import {
  PlusOutlined, DeleteOutlined, EditOutlined, PlayCircleOutlined,
  ClockCircleOutlined, ReloadOutlined, PauseCircleOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { apiFetch } from '@/lib/utils'

const PLATFORM_OPTIONS = [
  { value: 'chatgpt', label: '🤖 ChatGPT' },
  { value: 'grok', label: '⚡ Grok' },
  { value: 'trae', label: '💎 Trae.ai' },
  { value: 'kiro', label: '🔧 Kiro' },
  { value: 'openblocklabs', label: '🔬 OpenBlockLabs' },
]

const MAIL_OPTIONS = [
  { value: 'outlook', label: 'Outlook 邮箱池' },
  { value: 'cfworker', label: 'CF Worker 域名邮箱' },
]

interface Job {
  id: number; name: string; platform: string
  cron_hour: number; cron_minute: number; cron_display: string
  count: number; concurrency: number; mail_provider: string
  proxy: string; enabled: boolean
  last_run_at: string; next_run_at: string; created_at: string
}

export default function ScheduledJobs() {
  const { message: msg } = App.useApp()
  const { token: t } = theme.useToken()
  const [jobs, setJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [editRecord, setEditRecord] = useState<Job | null>(null)
  const [form] = Form.useForm()
  const [busyMap, setBusyMap] = useState<Record<string, boolean>>({})

  const load = async () => {
    setLoading(true)
    try {
      const [data, status] = await Promise.all([
        apiFetch('/scheduled/jobs'),
        apiFetch('/scheduled/jobs/status'),
      ])
      setJobs(data || [])
      setBusyMap(Object.fromEntries(
        Object.entries(status || {}).map(([k, v]: any) => [k, v.busy])
      ))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const openCreate = () => {
    setEditRecord(null)
    form.resetFields()
    form.setFieldsValue({
      platform: 'chatgpt', cron_time: dayjs('09:00', 'HH:mm'),
      count: 1, concurrency: 1, mail_provider: 'outlook', proxy: 'http://127.0.0.1:7890',
      enabled: true,
    })
    setEditOpen(true)
  }

  const openEdit = (record: Job) => {
    setEditRecord(record)
    form.setFieldsValue({
      ...record,
      cron_time: dayjs(`${String(record.cron_hour).padStart(2, '0')}:${String(record.cron_minute).padStart(2, '0')}`, 'HH:mm'),
    })
    setEditOpen(true)
  }

  const handleSave = async () => {
    const values = await form.validateFields()
    const time = values.cron_time as dayjs.Dayjs
    const body = {
      name: values.name || '',
      platform: values.platform,
      cron_hour: time.hour(),
      cron_minute: time.minute(),
      count: values.count,
      concurrency: values.concurrency,
      mail_provider: values.mail_provider,
      proxy: values.proxy || '',
      enabled: values.enabled,
    }
    if (editRecord) {
      await apiFetch(`/scheduled/jobs/${editRecord.id}`, { method: 'PUT', body: JSON.stringify(body) })
      msg.success('更新成功')
    } else {
      await apiFetch('/scheduled/jobs', { method: 'POST', body: JSON.stringify(body) })
      msg.success('创建成功')
    }
    setEditOpen(false)
    load()
  }

  const handleDelete = async (id: number) => {
    await apiFetch(`/scheduled/jobs/${id}`, { method: 'DELETE' })
    msg.success('已删除')
    load()
  }

  const handleToggle = async (id: number) => {
    await apiFetch(`/scheduled/jobs/${id}/toggle`, { method: 'POST' })
    load()
  }

  const handleRunNow = async (job: Job) => {
    const res = await apiFetch(`/scheduled/jobs/${job.id}/run-now`, { method: 'POST' })
    if (res.ok) {
      msg.success(`已触发: ${job.name}`)
      load()
    } else {
      msg.error(res.error || '执行失败')
    }
  }

  const columns: any[] = [
    {
      title: '计划名称', dataIndex: 'name', key: 'name', width: 180,
      render: (name: string, r: Job) => (
        <Space>
          <span>{PLATFORM_OPTIONS.find(p => p.value === r.platform)?.label?.slice(0, 2)}</span>
          <span style={{ fontWeight: 500 }}>{name || `${r.platform} 定时注册`}</span>
        </Space>
      ),
    },
    {
      title: '执行时间', dataIndex: 'cron_display', key: 'cron_display', width: 100,
      render: (text: string) => <Tag icon={<ClockCircleOutlined />} color="blue">{text}</Tag>,
    },
    {
      title: '数量', dataIndex: 'count', key: 'count', width: 60, align: 'center' as const,
    },
    {
      title: '并发', dataIndex: 'concurrency', key: 'concurrency', width: 60, align: 'center' as const,
    },
    {
      title: '邮箱', dataIndex: 'mail_provider', key: 'mail_provider', width: 100,
      render: (v: string) => <Tag>{v === 'outlook' ? 'Outlook' : 'CF Worker'}</Tag>,
    },
    {
      title: '状态', key: 'status', width: 80,
      render: (_: any, r: Job) => {
        const busy = busyMap[r.platform]
        return (
          <Space direction="vertical" size={0}>
            <Tag color={r.enabled ? 'success' : 'default'}>{r.enabled ? '启用' : '禁用'}</Tag>
            {busy && <Tag color="processing" icon={<ThunderboltOutlined />}>运行中</Tag>}
          </Space>
        )
      },
    },
    {
      title: '上次执行', dataIndex: 'last_run_at', key: 'last_run_at', width: 150,
      render: (v: string) => v ? new Date(v).toLocaleString() : '-',
    },
    {
      title: '下次执行', dataIndex: 'next_run_at', key: 'next_run_at', width: 150,
      render: (v: string, r: Job) => r.enabled && v ? new Date(v).toLocaleString() : '-',
    },
    {
      title: '操作', key: 'action', width: 160, fixed: 'right' as const,
      render: (_: any, r: Job) => (
        <Space size={0}>
          <Tooltip title="立即执行"><Button type="text" size="small" icon={<PlayCircleOutlined />} onClick={() => handleRunNow(r)} disabled={busyMap[r.platform]} /></Tooltip>
          <Tooltip title={r.enabled ? '暂停' : '启用'}><Button type="text" size="small" icon={r.enabled ? <PauseCircleOutlined /> : <PlayCircleOutlined style={{ color: t.colorSuccess }} />} onClick={() => handleToggle(r.id)} /></Tooltip>
          <Button type="text" size="small" icon={<EditOutlined />} onClick={() => openEdit(r)} />
          <Popconfirm title="确认删除？" onConfirm={() => handleDelete(r.id)}>
            <Button type="text" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <PageHeader
        title="定时任务"
        subtitle="配置每日自动注册计划，支持多平台并行"
        icon={<ClockCircleOutlined />}
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建计划</Button>
          </Space>
        }
      />

      <Card>
        <Table
          rowKey="id" columns={columns} dataSource={jobs}
          loading={loading} size="middle" pagination={false}
          scroll={{ x: 1100 }}
        />
      </Card>

      <Modal
        title={editRecord ? '编辑定时计划' : '新建定时计划'}
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={handleSave}
        width={560}
        maskClosable={false}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="platform" label="平台" rules={[{ required: true }]}>
                <Select options={PLATFORM_OPTIONS} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="name" label="计划名称">
                <Input placeholder="自动填充" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item name="cron_time" label="每日执行时间" rules={[{ required: true }]}>
                <TimePicker format="HH:mm" style={{ width: '100%' }} minuteStep={5} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="count" label="注册数量" rules={[{ required: true }]}>
                <InputNumber min={1} max={50} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="concurrency" label="并发数" rules={[{ required: true }]}>
                <InputNumber min={1} max={5} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="mail_provider" label="邮箱来源">
                <Select options={MAIL_OPTIONS} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="proxy" label="代理">
                <Input placeholder="http://127.0.0.1:7890" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="enabled" label="创建后立即启用" valuePropName="checked">
            <Switch checkedChildren="启用" unCheckedChildren="暂停" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
