export const ticketsViewTemplate = `
<section class="panel">
  <h2>过磅票采集</h2>
  <p class="hint">录入各轴重 JSON；超重自动旁路挂起并开超重记录。</p>
  <div class="form-row">
    <div><label>车道</label>
      <select v-model.number="ticketForm.lane_id">
        <option v-for="l in lanes" :key="l.id" :value="l.id">{{ l.lane_code }}</option>
      </select>
    </div>
    <div><label>提单号</label>
      <select v-model="ticketForm.bl_ref">
        <option v-for="b in blList" :key="b.bl_code" :value="b.bl_code">{{ b.bl_code }}</option>
      </select>
    </div>
    <div><label>车牌</label><input v-model="ticketForm.vehicle_plate"/></div>
    <div><label>票号(可空)</label><input v-model="ticketForm.ticket_code"/></div>
  </div>
  <label>轴重 JSON</label>
  <textarea v-model="ticketForm.axles_json" rows="4" placeholder='[{"axle":1,"w":8.2}]'></textarea>
  <div class="actions">
    <button @click="createTicket">过磅入闸</button>
    <button class="secondary" @click="refresh">刷新</button>
  </div>
  <table class="data-table">
    <thead><tr><th>票号</th><th>车道</th><th>提单</th><th>毛重</th><th>状态</th><th>代</th><th></th></tr></thead>
    <tbody>
      <tr v-for="t in tickets" :key="t.id" @click="selectTicket(t)">
        <td>{{ t.ticket_code }}</td>
        <td>{{ t.lane_code }}</td>
        <td>{{ t.bl_ref }}</td>
        <td>{{ fmtT(t.gross_t) }}</td>
        <td><span class="badge" :class="ticketClass(t.status)">{{ t.status }}</span></td>
        <td>{{ t.generation }}</td>
        <td><button class="mini-btn" @click.stop="loadTicketDetail(t.id)">详情</button></td>
      </tr>
    </tbody>
  </table>
  <div v-if="ticketDetail" class="detail-box">
    <p>车牌 {{ ticketDetail.ticket.vehicle_plate }} · 超重挂起 {{ ticketDetail.overload_holds.length }} 条</p>
    <div v-if="ticketDetail.axle_state" class="detail-box">
      <p>
        轴重状态：
        <span v-if="ticketDetail.axle_state.sealed" class="badge warn">已封闸 · 轴重锁定</span>
        <span v-else class="badge pass">未封闸</span>
        <span v-if="ticketDetail.axle_state.sealed"> · 裁决路径 {{ ticketDetail.axle_state.ruling_path }}</span>
        <span v-if="ticketDetail.axle_state.sealed && !ticketDetail.axle_state.axle_coherent" class="badge fail">封后轴重偏航</span>
      </p>
      <p class="hint">封后正式轴重表零改；修订只落影子草稿，待解封校验通过方可原子晋升。</p>
      <div class="form-row">
        <div><label>封后修订轴重 JSON（乙路径草稿）</label>
          <textarea v-model="axleDraftInput" rows="3" placeholder='[{"axle":1,"w":13.5}]'></textarea>
        </div>
      </div>
      <div class="actions" v-if="ticketDetail.axle_state.sealed || ticketDetail.axle_state.pending_drafts">
        <button v-if="ticketDetail.axle_state.sealed" @click="submitAxleDraft(ticketDetail.ticket.id)">落影子草稿</button>
        <button v-if="ticketDetail.axle_state.sealed && ticketDetail.gate_seals.length"
                class="secondary" @click="unsealSeal(ticketDetail.gate_seals[0].seal_ref, '解封核校')">解封校验</button>
      </div>
      <p v-if="ticketDetail.axle_state.pending_drafts" class="recon-warn">影子草稿 {{ ticketDetail.axle_state.pending_drafts }} 份待解封校验</p>
      <ul v-if="ticketDetail.axle_deviations.length">
        <li v-for="d in ticketDetail.axle_deviations.slice(0,5)" :key="d.id" class="recon-warn">
          {{ d.attempted }} → {{ d.outcome }}（{{ d.ruling_path }}路径 · {{ d.actor }}）
        </li>
      </ul>
      <table v-if="ticketDetail.axle_drafts.length" class="data-table compact">
        <thead><tr><th>草稿</th><th>毛重</th><th>状态</th><th>备注</th><th></th></tr></thead>
        <tbody>
          <tr v-for="d in ticketDetail.axle_drafts" :key="d.id">
            <td class="mono">#{{ d.id }}</td>
            <td>{{ fmtT(d.gross_t) }}</td>
            <td><span class="badge" :class="d.status==='已晋升'?'pass':'warn'">{{ d.status }}</span></td>
            <td>{{ shortText(d.note) }}</td>
            <td>
              <button v-if="d.status==='待解封校验'" class="mini-btn" @click="promoteDraft(d.id)">晋升</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</section>
`;