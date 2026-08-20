# Show Me Sequence

一个用于生成清晰、简洁 UML 时序图的 Codex Skill，适用于接口调用、审批流程、订单履约、事件流等业务场景。

## 图表特点

- 每个步骤都有明确的指向箭头
- 箭头上方写中文业务步骤
- 箭头下方只写英文核心方法名
- 返回步骤使用虚线，调用步骤使用实线
- 自动整理阶段、分支、循环和参与者布局
- 支持输出 SVG 和高清 PNG

## 参考效果

![Agent FC 时序图](docs/images/agent-fc-sequence.png)

![退货全流程时序图](docs/images/refund-sequence.png)

## 安装

```bash
git clone https://github.com/Layau-code/show-me-sequence.git \
  ~/.codex/skills/show-me-sequence
```

重启 Codex 后即可使用。

## 使用

直接告诉 Codex：

```text
使用 $show-me-sequence，把下面的业务流程生成 UML 时序图：
用户提交订单，订单服务创建订单，库存服务锁定库存，支付服务完成扣款。
```

Codex 会生成可继续编辑的 JSON、SVG，并按需生成 PNG。

## 手动渲染

```bash
python3 scripts/render_sequence.py input.json output.svg --preset web
```

完整字段说明见 [references/specification.md](references/specification.md)。
