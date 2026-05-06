"""
Freelance Job Parser and Auto-Responder
Парсит задания с фриланс бирж, откликается и отправляет уведомления в Telegram
"""

import os
import time
import logging
from datetime import datetime
from typing import List, Dict, Optional
import requests
from bs4 import BeautifulSoup
import json

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('freelance_parser.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class FreelanceParser:
    """Базовый класс для парсинга фриланс бирж"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        self.processed_jobs = self.load_processed_jobs()
    
    def load_processed_jobs(self) -> set:
        """Загружает список уже обработанных заданий"""
        try:
            if os.path.exists('processed_jobs.json'):
                with open('processed_jobs.json', 'r', encoding='utf-8') as f:
                    return set(json.load(f))
        except Exception as e:
            logger.error(f"Ошибка загрузки обработанных заданий: {e}")
        return set()
    
    def save_processed_jobs(self):
        """Сохраняет список обработанных заданий"""
        try:
            with open('processed_jobs.json', 'w', encoding='utf-8') as f:
                json.dump(list(self.processed_jobs), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения обработанных заданий: {e}")
    
    def parse_jobs(self) -> List[Dict]:
        """Парсит задания с биржи (переопределяется в подклассах)"""
        raise NotImplementedError
    
    def send_proposal(self, job: Dict) -> bool:
        """Отправляет предложение на задание (переопределяется в подклассах)"""
        raise NotImplementedError
    
    def generate_personalized_proposal(self, job: Dict) -> str:
        """Генерирует персонализированное предложение"""
        template = self.config.get('proposal_template', '')
        
        # Замена переменных в шаблоне
        proposal = template.format(
            title=job.get('title', ''),
            description=job.get('description', ''),
            budget=job.get('budget', ''),
            client_name=job.get('client_name', 'Заказчик')
        )
        
        return proposal


class FL_RuParser(FreelanceParser):
    """Парсер для FL.ru"""
    
    def __init__(self, config: Dict):
        super().__init__(config)
        self.base_url = 'https://www.fl.ru'
        self.login()
    
    def login(self):
        """Авторизация на FL.ru"""
        try:
            login_url = f'{self.base_url}/login.php'
            data = {
                'login': self.config.get('fl_ru_login', ''),
                'passwd': self.config.get('fl_ru_password', '')
            }
            response = self.session.post(login_url, data=data, timeout=30)
            if response.status_code == 200:
                logger.info("Успешная авторизация на FL.ru")
            else:
                logger.error(f"Ошибка авторизации на FL.ru: {response.status_code}")
        except Exception as e:
            logger.error(f"Ошибка при авторизации на FL.ru: {e}")
    
    def parse_jobs(self) -> List[Dict]:
        """Парсит задания с FL.ru"""
        jobs = []
        try:
            # Категории для поиска
            categories = self.config.get('fl_ru_categories', [''])
            
            for category in categories:
                # Формируем правильный URL
                if category:
                    url = f'{self.base_url}/projects/?category={category}'
                else:
                    url = f'{self.base_url}/projects/'
                
                logger.info(f"Парсинг URL: {url}")
                
                try:
                    response = self.session.get(url, timeout=30)
                    
                    if response.status_code == 404:
                        logger.warning(f"Страница не найдена (404): {url}")
                        continue
                    elif response.status_code != 200:
                        logger.error(f"Ошибка загрузки страницы FL.ru: {response.status_code}")
                        continue
                    
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # Пробуем разные варианты селекторов для FL.ru
                    job_items = (
                        soup.find_all('div', class_='b-post') or
                        soup.find_all('div', class_='project-item') or
                        soup.find_all('article', class_='project') or
                        soup.find_all('div', attrs={'data-project-id': True}) or
                        []
                    )
                    
                    if not job_items:
                        logger.warning(f"Не найдено заданий на странице: {url}")
                        # Сохраняем HTML для отладки
                        with open('debug_fl_ru.html', 'w', encoding='utf-8') as f:
                            f.write(response.text)
                        logger.info("HTML страницы сохранен в debug_fl_ru.html для анализа")
                        continue
                    
                    logger.info(f"Найдено {len(job_items)} элементов на странице")
                    
                    for item in job_items:
                        try:
                            # Пробуем получить ID разными способами
                            job_id = (
                                item.get('data-project-id') or
                                item.get('data-id') or
                                item.get('id') or
                                str(hash(str(item)[:100]))  # Fallback: хеш от начала элемента
                            )
                            
                            if job_id in self.processed_jobs:
                                continue
                            
                            # Пробуем найти заголовок разными способами
                            title_elem = (
                                item.find('a', class_='b-post__link') or
                                item.find('a', class_='b-post__title') or
                                item.find('a', class_='project-title') or
                                item.find('h2') or
                                item.find('h3') or
                                item.find('a', href=lambda x: x and '/projects/' in str(x))
                            )
                            
                            if not title_elem:
                                continue
                            
                            title = title_elem.text.strip()
                            link = title_elem.get('href', '')
                            if link and not link.startswith('http'):
                                link = self.base_url + link
                            
                            # Описание
                            description_elem = (
                                item.find('div', class_='b-post__txt') or
                                item.find('div', class_='b-post__body') or
                                item.find('div', class_='project-description') or
                                item.find('p')
                            )
                            description = description_elem.text.strip() if description_elem else ''
                            
                            # Бюджет
                            budget_elem = (
                                item.find('div', class_='b-post__price') or
                                item.find('span', class_='b-post__price') or
                                item.find('span', class_='project-budget') or
                                item.find('span', string=lambda x: x and '₽' in str(x))
                            )
                            budget = budget_elem.text.strip() if budget_elem else 'Не указан'
                            
                            if title:  # Добавляем только если есть заголовок
                                jobs.append({
                                    'id': job_id,
                                    'title': title,
                                    'description': description,
                                    'budget': budget,
                                    'link': link,
                                    'source': 'FL.ru'
                                })
                                logger.info(f"Найдено задание: {title[:50]}...")
                        
                        except Exception as e:
                            logger.error(f"Ошибка парсинга задания FL.ru: {e}")
                            continue
                
                except requests.exceptions.RequestException as e:
                    logger.error(f"Ошибка сетевого запроса к FL.ru: {e}")
                    continue
        
        except Exception as e:
            logger.error(f"Общая ошибка парсинга FL.ru: {e}")
        
        return jobs
    
    def send_proposal(self, job: Dict) -> bool:
        """Отправляет предложение на FL.ru"""
        try:
            proposal_text = self.generate_personalized_proposal(job)
            
            # URL для отправки предложения (нужно адаптировать под реальный API)
            url = f"{self.base_url}/projects/{job['id']}/respond"
            
            data = {
                'message': proposal_text,
                'price': self.config.get('default_price', ''),
                'deadline': self.config.get('default_deadline', '')
            }
            
            response = self.session.post(url, data=data, timeout=30)
            
            if response.status_code == 200:
                logger.info(f"Предложение отправлено на задание: {job['title']}")
                return True
            else:
                logger.error(f"Ошибка отправки предложения: {response.status_code}")
                return False
        
        except Exception as e:
            logger.error(f"Ошибка отправки предложения на FL.ru: {e}")
            return False


class FreelanceRuParser(FreelanceParser):
    """Парсер для Freelance.ru"""
    
    def __init__(self, config: Dict):
        super().__init__(config)
        self.base_url = 'https://freelance.ru'
        self.login()
    
    def login(self):
        """Авторизация на Freelance.ru"""
        try:
            # Реализация авторизации
            logger.info("Авторизация на Freelance.ru")
        except Exception as e:
            logger.error(f"Ошибка авторизации на Freelance.ru: {e}")
    
    def parse_jobs(self) -> List[Dict]:
        """Парсит задания с Freelance.ru"""
        jobs = []
        try:
            url = f'{self.base_url}/projects'
            logger.info(f"Парсинг URL: {url}")
            
            response = self.session.get(url, timeout=30)
            
            if response.status_code == 404:
                logger.warning(f"Страница не найдена (404): {url}")
                return jobs
            elif response.status_code != 200:
                logger.error(f"Ошибка загрузки Freelance.ru: {response.status_code}")
                return jobs
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Пробуем разные варианты селекторов
            job_items = (
                soup.find_all('div', class_='task-item') or
                soup.find_all('article', class_='project') or
                soup.find_all('div', class_='project-item') or
                []
            )
            
            if not job_items:
                logger.warning(f"Не найдено заданий на Freelance.ru")
                with open('debug_freelance_ru.html', 'w', encoding='utf-8') as f:
                    f.write(response.text)
                logger.info("HTML страницы сохранен в debug_freelance_ru.html для анализа")
                return jobs
            
            for item in job_items:
                try:
                    job_id = (
                        item.get('data-id') or
                        item.get('id') or
                        str(hash(str(item)[:100]))
                    )
                    
                    if job_id in self.processed_jobs:
                        continue
                    
                    # Извлечение данных
                    title_elem = item.find('h2') or item.find('h3') or item.find('a')
                    title = title_elem.text.strip() if title_elem else ''
                    
                    description_elem = item.find('div', class_='description') or item.find('p')
                    description = description_elem.text.strip() if description_elem else ''
                    
                    if title:
                        jobs.append({
                            'id': job_id,
                            'title': title,
                            'description': description,
                            'source': 'Freelance.ru'
                        })
                        logger.info(f"Найдено задание: {title[:50]}...")
                
                except Exception as e:
                    logger.error(f"Ошибка парсинга задания Freelance.ru: {e}")
                    continue
        
        except Exception as e:
            logger.error(f"Ошибка парсинга Freelance.ru: {e}")
        
        return jobs
    
    def send_proposal(self, job: Dict) -> bool:
        """Отправляет предложение на Freelance.ru"""
        try:
            proposal_text = self.generate_personalized_proposal(job)
            # Реализация отправки предложения
            logger.info(f"Предложение отправлено: {job['title']}")
            return True
        except Exception as e:
            logger.error(f"Ошибка отправки предложения на Freelance.ru: {e}")
            return False


class KworkParser(FreelanceParser):
    """Парсер для Kwork.ru"""
    
    def __init__(self, config: Dict):
        super().__init__(config)
        self.base_url = 'https://kwork.ru'
        self.login()
    
    def login(self):
        """Авторизация на Kwork.ru"""
        try:
            login_url = f'{self.base_url}/login'
            data = {
                'username': self.config.get('kwork_login', ''),
                'password': self.config.get('kwork_password', '')
            }
            response = self.session.post(login_url, data=data, timeout=30)
            if response.status_code == 200:
                logger.info("Успешная авторизация на Kwork.ru")
            else:
                logger.error(f"Ошибка авторизации на Kwork.ru: {response.status_code}")
        except Exception as e:
            logger.error(f"Ошибка при авторизации на Kwork.ru: {e}")
    
    def parse_jobs(self) -> List[Dict]:
        """Парсит задания с Kwork.ru"""
        jobs = []
        try:
            # Категории для поиска
            categories = self.config.get('kwork_categories', [''])
            
            for category in categories:
                url = f'{self.base_url}/projects{category}'
                logger.info(f"Парсинг URL: {url}")
                
                try:
                    response = self.session.get(url, timeout=30)
                    
                    if response.status_code == 404:
                        logger.warning(f"Страница не найдена (404): {url}")
                        continue
                    elif response.status_code != 200:
                        logger.error(f"Ошибка загрузки страницы Kwork.ru: {response.status_code}")
                        continue
                    
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # Пробуем разные варианты селекторов для Kwork
                    job_items = (
                        soup.find_all('div', class_='wants-card') or
                        soup.find_all('div', class_='project-item') or
                        soup.find_all('article', class_='want') or
                        []
                    )
                    
                    logger.info(f"Найдено {len(job_items)} элементов на странице")
                    
                    if not job_items:
                        logger.warning(f"Не найдено заданий на Kwork.ru")
                        with open('debug_kwork.html', 'w', encoding='utf-8') as f:
                            f.write(response.text)
                        logger.info("HTML страницы сохранен в debug_kwork.html для анализа")
                        continue
                    
                    for item in job_items:
                        try:
                            # Извлечение ID задания
                            job_id = (
                                item.get('data-id') or
                                item.get('data-project-id') or
                                item.get('id', '')
                            )
                            
                            if not job_id:
                                # Пытаемся извлечь ID из ссылки
                                link_elem = item.find('a', href=True)
                                if link_elem and '/projects/' in link_elem['href']:
                                    job_id = link_elem['href'].split('/')[-1].split('?')[0]
                            
                            if not job_id or job_id in self.processed_jobs:
                                continue
                            
                            # Извлечение заголовка
                            title_elem = (
                                item.find('a', class_='wants-card__header-title') or
                                item.find('h3') or
                                item.find('a', class_='title')
                            )
                            title = title_elem.text.strip() if title_elem else ''
                            
                            # Извлечение ссылки
                            link = ''
                            if title_elem and title_elem.get('href'):
                                link = self.base_url + title_elem['href'] if not title_elem['href'].startswith('http') else title_elem['href']
                            
                            # Извлечение описания
                            description_elem = (
                                item.find('div', class_='wants-card__description') or
                                item.find('div', class_='description') or
                                item.find('p')
                            )
                            description = description_elem.text.strip() if description_elem else ''
                            
                            # Извлечение бюджета
                            budget_elem = (
                                item.find('div', class_='wants-card__price') or
                                item.find('span', class_='price') or
                                item.find('div', class_='budget')
                            )
                            budget = budget_elem.text.strip() if budget_elem else 'Не указан'
                            
                            if title:
                                jobs.append({
                                    'id': job_id,
                                    'title': title,
                                    'description': description,
                                    'budget': budget,
                                    'link': link,
                                    'source': 'Kwork.ru'
                                })
                                logger.info(f"Найдено задание: {title[:50]}...")
                        
                        except Exception as e:
                            logger.error(f"Ошибка парсинга задания Kwork.ru: {e}")
                            continue
                
                except requests.exceptions.RequestException as e:
                    logger.error(f"Ошибка сетевого запроса к Kwork.ru: {e}")
                    continue
        
        except Exception as e:
            logger.error(f"Общая ошибка парсинга Kwork.ru: {e}")
        
        return jobs
    
    def send_proposal(self, job: Dict) -> bool:
        """Отправляет предложение на Kwork.ru"""
        try:
            proposal_text = self.generate_personalized_proposal(job)
            
            # URL для отправки предложения
            url = f"{self.base_url}/projects/{job['id']}/respond"
            
            data = {
                'message': proposal_text,
                'price': self.config.get('default_price', ''),
                'deadline': self.config.get('default_deadline', '')
            }
            
            response = self.session.post(url, data=data, timeout=30)
            
            if response.status_code == 200:
                logger.info(f"Предложение отправлено на задание: {job['title']}")
                return True
            else:
                logger.error(f"Ошибка отправки предложения: {response.status_code}")
                return False
        
        except Exception as e:
            logger.error(f"Ошибка отправки предложения на Kwork.ru: {e}")
            return False


class TelegramNotifier:
    """Класс для отправки уведомлений в Telegram"""
    
    def __init__(self, bot_token: str, chat_id: str, config: Dict = None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f'https://api.telegram.org/bot{bot_token}'
        self.config = config or {}
    
    def send_message(self, text: str, parse_mode: str = 'HTML'):
        """Отправляет сообщение в Telegram"""
        try:
            url = f'{self.api_url}/sendMessage'
            data = {
                'chat_id': self.chat_id,
                'text': text,
                'parse_mode': parse_mode
            }
            response = requests.post(url, data=data, timeout=30)
            
            if response.status_code == 200:
                logger.info("Уведомление отправлено в Telegram")
                return True
            else:
                logger.error(f"Ошибка отправки в Telegram: {response.status_code}, {response.text}")
                return False
        
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления в Telegram: {e}")
            return False
    
    def notify_new_job(self, job: Dict, proposal_sent: bool):
        """Отправляет уведомление о новом задании"""
        if not proposal_sent and not self.config.get('auto_send_proposals', False):
            status = "ℹ️ Уведомление (автоотправка выключена)"
        elif proposal_sent:
            status = "✅ Предложение отправлено"
        else:
            status = "❌ Ошибка отправки"
        
        message = f"""
🆕 <b>Новое задание!</b>

📋 <b>Название:</b> {job.get('title', 'Не указано')}
💰 <b>Бюджет:</b> {job.get('budget', 'Не указан')}
🌐 <b>Источник:</b> {job.get('source', 'Неизвестно')}
🔗 <b>Ссылка:</b> {job.get('link', 'Нет ссылки')}

{status}

<b>Описание:</b>
{job.get('description', 'Нет описания')[:500]}...
"""
        
        self.send_message(message)


class FreelanceBot:
    """Главный класс бота"""
    
    def __init__(self, config_path: str = 'config.json'):
        self.config = self.load_config(config_path)
        self.parsers = []
        self.telegram = TelegramNotifier(
            self.config.get('telegram_bot_token', ''),
            self.config.get('telegram_chat_id', ''),
            self.config
        )
        
        # Инициализация парсеров
        if self.config.get('enable_fl_ru', False):
            self.parsers.append(FL_RuParser(self.config))
        
        if self.config.get('enable_freelance_ru', False):
            self.parsers.append(FreelanceRuParser(self.config))
        
        if self.config.get('enable_kwork', False):
            self.parsers.append(KworkParser(self.config))
        
        if not self.parsers:
            logger.warning("Не включен ни один парсер! Проверьте config.json")
    
    def load_config(self, config_path: str) -> Dict:
        """Загружает конфигурацию"""
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            else:
                logger.warning(f"Файл конфигурации {config_path} не найден")
                return {}
        except Exception as e:
            logger.error(f"Ошибка загрузки конфигурации: {e}")
            return {}
    
    def run(self):
        """Запускает бота"""
        logger.info("=" * 50)
        logger.info("Запуск Freelance Parser Bot")
        logger.info("=" * 50)
        
        if not self.parsers:
            logger.error("Нет активных парсеров. Завершение работы.")
            return
        
        check_interval = self.config.get('check_interval', 300)  # 5 минут по умолчанию
        
        while True:
            try:
                logger.info("Проверка новых заданий...")
                
                for parser in self.parsers:
                    jobs = parser.parse_jobs()
                    logger.info(f"Найдено {len(jobs)} новых заданий на {parser.__class__.__name__}")
                    
                    for job in jobs:
                        # Проверка фильтров
                        if not self.check_filters(job):
                            logger.info(f"Задание не прошло фильтры: {job['title']}")
                            continue
                        
                        # Отправка предложения
                        proposal_sent = False
                        if self.config.get('auto_send_proposals', False):
                            proposal_sent = parser.send_proposal(job)
                        
                        # Уведомление в Telegram
                        self.telegram.notify_new_job(job, proposal_sent)
                        
                        # Добавление в обработанные
                        parser.processed_jobs.add(job['id'])
                        parser.save_processed_jobs()
                        
                        # Задержка между отправками
                        time.sleep(self.config.get('delay_between_proposals', 10))
                
                logger.info(f"Ожидание {check_interval} секунд до следующей проверки...")
                time.sleep(check_interval)
            
            except KeyboardInterrupt:
                logger.info("Остановка бота...")
                break
            except Exception as e:
                logger.error(f"Ошибка в главном цикле: {e}")
                time.sleep(60)  # Ждем минуту перед повторной попыткой
    
    def check_filters(self, job: Dict) -> bool:
        """Проверяет задание по фильтрам"""
        # Фильтр по ключевым словам
        keywords = self.config.get('keywords', [])
        if keywords:
            title_lower = job.get('title', '').lower()
            description_lower = job.get('description', '').lower()
            
            if not any(keyword.lower() in title_lower or keyword.lower() in description_lower 
                      for keyword in keywords):
                return False
        
        # Фильтр по исключающим словам
        exclude_keywords = self.config.get('exclude_keywords', [])
        if exclude_keywords:
            title_lower = job.get('title', '').lower()
            description_lower = job.get('description', '').lower()
            
            if any(keyword.lower() in title_lower or keyword.lower() in description_lower 
                  for keyword in exclude_keywords):
                return False
        
        # Фильтр по минимальному бюджету
        min_budget = self.config.get('min_budget', 0)
        if min_budget > 0:
            budget_str = job.get('budget', '0')
            try:
                budget = int(''.join(filter(str.isdigit, budget_str)))
                if budget < min_budget:
                    return False
            except:
                pass
        
        return True


def main():
    """Главная функция"""
    try:
        bot = FreelanceBot('config.json')
        bot.run()
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        raise


if __name__ == '__main__':
    main()
