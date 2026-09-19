import { initModules } from './manager'
import { log } from './api'

initModules().catch((err: unknown) => log.error('模块初始化失败:', err))