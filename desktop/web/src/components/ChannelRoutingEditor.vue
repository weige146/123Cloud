// 频道卡片网格 + 自动路由选择区：模板与样式源自 ChannelSettingsView，
// 编辑状态由 useChannelConfigEditor 提供并通过 editor prop 注入。
<script setup lang="ts">
import type { ChannelConfigEditor } from "@/composables/useChannelConfigEditor";

defineProps<{ editor: ChannelConfigEditor }>();

const roleItems = [
  { title: "私有频道", value: "private" },
  { title: "公开频道（完结内容）", value: "public_completed" },
  { title: "公开频道（连载内容）", value: "public_updating" },
];
</script>

<template>
  <div class="routing-editor">
    <!-- 频道列表 -->
    <section class="glass-section">
      <header class="section-head">
        <div class="section-head-left">
          <div class="section-icon section-icon--group"><v-icon size="18">mdi-broadcast</v-icon></div>
          <div>
            <h2>频道</h2>
            <p>名称只供你识别；Chat ID 是 Telegram 频道的数字 ID，例如 <code>-100xxxxxxxxxx</code>。</p>
          </div>
        </div>
        <v-btn color="primary" prepend-icon="mdi-plus" @click="editor.addChannel()">添加频道</v-btn>
      </header>

      <div class="channel-grid">
        <article v-for="(channel, index) in editor.state.channels" :key="channel.id" class="channel-card" :class="{ 'channel-card--default': channel.isDefault }">
          <div class="channel-card-head">
            <div class="channel-card-title">
              <span class="channel-card-index">#{{ index + 1 }}</span>
              <strong v-if="channel.title">{{ channel.title }}</strong>
              <strong v-else class="muted">未命名频道</strong>
              <span v-if="channel.isDefault" class="default-badge">
                <v-icon size="11">mdi-star</v-icon>
                默认
              </span>
            </div>
            <div class="channel-actions">
              <v-switch
                :model-value="channel.isDefault"
                label="默认投稿"
                color="primary"
                density="compact"
                hide-details
                @update:model-value="editor.setDefault(channel, Boolean($event))"
              />
              <v-btn size="small" color="error" variant="text" icon="mdi-delete-outline" aria-label="删除频道" @click="editor.removeChannel(channel)" />
            </div>
          </div>
          <div class="field-grid">
            <v-text-field v-model="channel.title" label="显示名称" placeholder="例如：我的私有频道" variant="outlined" density="comfortable" hide-details />
            <v-text-field v-model="channel.chatId" label="频道 Chat ID" placeholder="-100xxxxxxxxxx" variant="outlined" density="comfortable" hide-details />
            <v-select v-model="channel.role" label="频道类型" :items="roleItems" variant="outlined" density="comfortable" hide-details />
            <v-switch v-model="channel.enabled" label="启用这个频道" color="primary" density="comfortable" hide-details />
          </div>
          <v-textarea
            v-model="channel.collaboratorText"
            label="允许投稿的 Telegram UID（可留空）"
            placeholder="每行一个 UID；这些人只能投稿到这个频道"
            :rows="2"
            variant="outlined"
            density="comfortable"
            hide-details
            class="collaborator-field"
          />
        </article>
      </div>
    </section>

    <!-- 自动路由：语义与后端 select_submission_channel 一致 -->
    <section class="glass-section">
      <header class="section-head">
        <div class="section-head-left">
          <div class="section-icon section-icon--info"><v-icon size="18">mdi-routes-outline</v-icon></div>
          <div>
            <h2>自动路由</h2>
            <p>内容带发布组时先比对发布组白名单：命中白名单的按完结 / 连载规则投递（一般是公开频道），未命中的走“发布组不在白名单”规则；没有发布组的内容直接按完结 / 连载投递。</p>
          </div>
        </div>
      </header>
      <div class="route-grid">
        <div class="route-item">
          <div class="route-icon route-icon--warning"><v-icon size="16">mdi-shield-outline</v-icon></div>
          <div class="route-body">
            <label>发布组不在白名单时投递到</label>
            <v-select v-model="editor.state.routing.releaseGroupChannelId" :items="editor.channelOptions" variant="outlined" density="comfortable" hide-details />
            <small>带发布组、但没进白名单的内容（一般投私有频道）。</small>
          </div>
        </div>
        <div class="route-item">
          <div class="route-icon route-icon--success"><v-icon size="16">mdi-check-circle</v-icon></div>
          <div class="route-body">
            <label>完结内容投递到</label>
            <v-select v-model="editor.state.routing.noReleaseGroupCompletedChannelId" :items="editor.channelOptions" variant="outlined" density="comfortable" hide-details />
            <small>无发布组、或发布组命中白名单的完结内容。</small>
          </div>
        </div>
        <div class="route-item">
          <div class="route-icon route-icon--info"><v-icon size="16">mdi-progress-clock</v-icon></div>
          <div class="route-body">
            <label>连载内容投递到</label>
            <v-select v-model="editor.state.routing.noReleaseGroupUpdatingChannelId" :items="editor.channelOptions" variant="outlined" density="comfortable" hide-details />
            <small>无发布组、或发布组命中白名单的连载内容。</small>
          </div>
        </div>
      </div>
      <v-textarea
        v-model="editor.state.releaseGroupsText"
        label="发布组白名单"
        placeholder="每行一个发布组名称；命中白名单的内容按完结 / 连载规则投递，未命中的走“发布组不在白名单”规则"
        :rows="3"
        variant="outlined"
        density="comfortable"
        hide-details
        class="collaborator-field"
      />
    </section>
  </div>
</template>

<style scoped>
.glass-section {
  padding: 20px;
  margin-bottom: 16px;
  background: var(--surface-subtle);
  border: 1px solid var(--border);
  border-radius: var(--radius-surface);
  box-shadow: var(--surface-shadow);
  position: relative;
  overflow: hidden;
}

.glass-section::before {
  content: '';
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  background: var(--group-gradient);
  opacity: 0.85;
}

.section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 14px;
}

.section-head-left {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  flex: 1;
  min-width: 0;
}

.section-icon {
  width: 36px;
  height: 36px;
  border-radius: var(--radius-md);
  background: var(--group-gradient);
  color: #fff;
  display: grid;
  place-items: center;
  flex-shrink: 0;
  box-shadow: 0 4px 12px var(--group-glow);
}

.section-icon--info { background: linear-gradient(135deg, #3b82f6, #60a5fa); box-shadow: 0 4px 12px rgba(59, 130, 246, 0.28); }

.section-head h2 {
  margin: 0 0 3px;
  font-size: 16px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.3;
}

.section-head p {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--text-muted);
}

.section-head code {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--accent-soft);
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--accent);
}

/* 频道卡片网格 */
.channel-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 14px;
}

.channel-card {
  padding: 16px;
  background: var(--surface-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  transition: all var(--transition);
}

.channel-card:hover {
  border-color: var(--border-strong);
  box-shadow: var(--surface-shadow);
}

.channel-card--default {
  border-color: var(--group-color);
  background: linear-gradient(135deg, var(--surface-card), var(--group-soft));
}

.channel-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.channel-card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.channel-card-index {
  display: inline-grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: var(--radius-sm);
  background: var(--accent-soft);
  color: var(--accent);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
  flex-shrink: 0;
}

.channel-card-title strong {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.muted {
  color: var(--text-muted);
  font-weight: 500;
}

.default-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 8px;
  background: var(--group-gradient);
  color: #fff;
  font-size: 10px;
  font-weight: 600;
  border-radius: var(--radius-pill);
}

.channel-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.field-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.collaborator-field {
  margin-top: 12px;
}

/* 路由规则 */
.route-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.route-item {
  display: flex;
  gap: 12px;
  padding: 14px;
  background: var(--surface-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  transition: all var(--transition);
}

.route-item:hover {
  border-color: var(--border-strong);
}

.route-icon {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  display: grid;
  place-items: center;
  color: #fff;
  flex-shrink: 0;
}

.route-icon--group { background: var(--group-gradient); }
.route-icon--success { background: linear-gradient(135deg, #10b981, #34d399); }
.route-icon--info { background: linear-gradient(135deg, #3b82f6, #60a5fa); }
.route-icon--warning { background: linear-gradient(135deg, #f59e0b, #fbbf24); }

.route-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.route-body label {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--text-secondary);
}

.route-body small {
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-muted);
}

@media (max-width: 720px) {
  .field-grid,
  .route-grid {
    grid-template-columns: 1fr;
  }

  .channel-grid {
    grid-template-columns: 1fr;
  }

  .channel-card-head {
    flex-direction: column;
    align-items: flex-start;
  }

  .channel-actions {
    width: 100%;
    justify-content: space-between;
  }
}
</style>
