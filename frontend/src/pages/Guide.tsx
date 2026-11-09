import { Alert, Button, Card, Col, Collapse, Row, Space, Steps, Typography } from 'antd';
import { Link } from 'react-router-dom';
import { Heading } from '../components/Common';
import { useWorkbench } from '../context';

const questions = [
  {
    key: 'upload', label: '上传时刷新了页面，怎么办？',
    children: '回到仓库管理或案例集管理，重新选择原来的 ZIP，可从已保存的分块继续上传。文件全部上传后，后台才开始导入；等页面提示导入完成后再创建测评。',
  },
  {
    key: 'close', label: '关闭页面会停止测评吗？',
    children: '不会。已启动的后台任务会继续执行。需要停止时，在测评批次中打开对应任务并执行停止；重新打开页面可以查看进度和日志。',
  },
  {
    key: 'failure', label: '部分案例失败了，应该怎么做？',
    children: '先打开批次的“执行日志”查看失败原因。完整答卷会保留，评分只覆盖有效答卷，失败案例不当作零分。恢复已有答卷不重新检视；服务端 Agent 的恢复是查询原任务。补跑可能再次调用模型，是否支持补跑以批次页面提示为准。',
  },
  {
    key: 'compare', label: '怎么对比两个模型或 Agent？',
    children: '分别创建测评，尽量使用相同案例、测评目的、裁判和执行条件。完成后，在“测评批次”勾选 2–6 个批次，点击“对比结果”。对比读取已有结果，不重新调用模型；条件不一致时会提示。',
  },
  {
    key: 'purpose', label: '“检视质量”和“运行稳定性”怎么选？',
    children: '想比较能否准确找出问题，选“检视质量”，允许按配置重试。想看一次执行能否完成，选“运行稳定性”，关闭平台自动补跑。两种模式都保留首次结果和后续执行记录，创建后不能切换；稳定性模式也需要核对是否与实际线上运行条件一致。',
  },
  {
    key: 'resources', label: '换案例集，需要重新上传所有仓库吗？',
    children: '不需要。先确认仓库管理中已有案例所需的仓库和提交；有缺失时再更新对应仓库。仓库 ZIP 保存代码及 Git 历史，案例集 ZIP 保存案例配置，两者要上传到各自的管理页面。',
  },
  {
    key: 'special', label: 'MCR 和 c-CRAB 与普通测评有什么不同？',
    children: 'MCR 按多轮代码变化检查缺陷状态；c-CRAB 会检视代码、尝试修复并运行测试，主要看测试通过率，不使用 F1。这两类目前不支持新的“运行稳定性”模式，先使用普通单轮案例熟悉流程即可。',
  },
];

export function Guide() {
  const { newRun } = useWorkbench();
  const create = () => newRun();
  return (
    <>
      <Heading title="使用指南" description="第一次使用按下面四步操作；已有的仓库、案例集和模型配置可以直接复用。" />
      <Typography.Paragraph type="secondary">点击步骤可进入对应页面。“新建测评”只打开配置表单，不会立即运行。</Typography.Paragraph>
      <Steps className="section-gap" current={-1} items={[
        { title: <Link to="/repositories">准备仓库与案例</Link> },
        { title: <Link to="/models">配置模型</Link> },
        { title: <Button type="link" onClick={create}>新建测评</Button> },
        { title: <Link to="/">查看进度与报告</Link> },
      ]} />
      <Row gutter={[16, 16]} className="section-gap">
        <Col xs={24} lg={12}>
          <Card title="1. 准备仓库与案例">
            <ol>
              <li>在“仓库管理”上传包含 .git 历史的仓库 ZIP。</li>
              <li>在“案例集管理”上传案例集 ZIP，不能把仓库包传到这里。</li>
              <li>看到导入成功、案例集可选后，继续配置模型。</li>
            </ol>
            <Space wrap><Link to="/repositories">进入仓库管理</Link><Link to="/cases">进入案例集管理</Link></Space>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="2. 配置模型">
            <ol>
              <li>在“模型配置”点击“新增模型”，填写接口地址、模型 ID 和密钥。</li>
              <li>被测模型负责检视代码；裁判模型负责给检视结果评分，可以选同一个模型。</li>
              <li>保存配置后，可在新建测评时选择它。</li>
            </ol>
            <Link to="/models">进入模型配置</Link>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="3. 新建并开始测评">
            <ol>
              <li>选择案例范围、被测工具、被测模型和裁判模型。</li>
              <li>比较找问题的能力，选“检视质量”；观察一次执行能否完成，选“运行稳定性”。</li>
              <li>第一次建议先用小案例集，确认配置后点击“创建并开始”。</li>
            </ol>
            <Button type="primary" onClick={create}>新建测评</Button>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="4. 查看进度与报告">
            <ol>
              <li>进入“测评批次”，点击要查看的批次名称。</li>
              <li>运行时看“概览与案例”和“执行日志”；排队中表示任务还未开始。</li>
              <li>评分和报告生成后，打开“中文报告”，先看完成覆盖和主要指标，再看具体问题。</li>
            </ol>
            <Link to="/">进入测评批次</Link>
          </Card>
        </Col>
      </Row>
      <Alert className="section-gap" type="info" showIcon
        message="保存配置、上传资源和查看已有结果不会调用模型。开始检视、评分、补跑和测试模型连接可能产生 API 用量。" />
      <Typography.Title level={4}>常见问题</Typography.Title>
      <Collapse items={questions} />
    </>
  );
}
