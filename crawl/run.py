#! /usr/bin/env python

"""Script that identifies ads, the respective advertisers, and uploads this data to S3"""

from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys

from PIL import Image

import logging
import time, uuid, base64, json
import io, re, sys, os

import requests
import conf

import pdb

import boto3

RE_TWITTER = re.compile('twitter\.com\/(.+?)"')

logging.basicConfig(level=logging.DEBUG)

class Crawler:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.driver = webdriver.Chrome()
        # self.driver = webdriver.PhantomJS()
        self.driver.set_page_load_timeout(conf.PAGE_LOAD_TIMEOUT_SECONDS)
        self.driver.set_window_size(conf.WINDOW_WIDTH, conf.WINDOW_HEIGHT)

    def find_twitter_account(self, url):
        if url is not None:
            try:
                r = requests.get(url)
                twitter_accounts = RE_TWITTER.findall(r.content)
                if len(twitter_accounts) > 0:
                    return twitter_accounts[0]
            except Exception as e:
                logging.exception('Failed to fetch {0}'.format(url))
        return None

    def crawl(self):
        ads = []

        # Test with one for now
        url = conf.URLS[0]
        self.driver.get(url[0])

        time.sleep(conf.SLEEP_SECONDS)

        # Find ads
        els = self.driver.find_elements_by_class_name(url[1])

        main_window = self.driver.current_window_handle

        run_id = str(uuid.uuid4())

        os.mkdir(os.path.join(self.out_dir, run_id))

        for idx, el in enumerate(els):
            logging.info('Processing element: {0}'.format(idx))

            img_id = str(idx)

            filename = 'screenshot-' + img_id + '.jpg'
            filepath = os.path.join(self.out_dir, run_id, filename)

            # From http://stackoverflow.com/questions/15018372/how-to-take-partial-screenshot-with-selenium-webdriver-in-python
            # and http://stackoverflow.com/questions/37882208/get-element-location-relative-to-viewport-with-selenium-python
            self.driver.execute_script("return arguments[0].scrollIntoView();", el)
            self.driver.execute_script("window.scrollBy(0, -150);")
            self.driver.save_screenshot(filepath)

            scroll = self.driver.execute_script("return window.scrollY;")
            location = el.location
            size = el.size

            # No valid ad found so clean up
            if size['height'] == 0 or size['width'] == 0:
                os.remove(filepath)
                continue

            # Switch to main window
            self.driver.switch_to_window(main_window)

            # From http://stackoverflow.com/questions/27775759/send-keys-control-click-selenium
            # Cmd + Click to get to the new tab
            ActionChains(self.driver) \
                .key_down(Keys.COMMAND) \
                .click(el) \
                .key_up(Keys.COMMAND) \
                .perform()

            curr_url = None
            if len(self.driver.window_handles) > 1:
                # Switch to new tab, get url, and close it so we go back to the main window
                self.driver.switch_to_window(self.driver.window_handles[1])

                # Wait just in case, might be redirects or just slow
                time.sleep(conf.SLEEP_SECONDS/5)

                curr_url = self.driver.current_url
                logging.info('Current URL {0}'.format(curr_url))

                self.driver.close()
            else:
                logging.error('Unable to switch to new tab')

            self.driver.switch_to_window(main_window)

            im = Image.open(filepath)

            left = location['x']
            top = location['y'] - scroll
            right = location['x'] + size['width']
            bottom = location['y'] + size['height'] - scroll

            logging.debug('Scroll: {}'.format(scroll))
            logging.debug('Size: {}'.format(size))
            logging.debug('Location: {}'.format(location))

            filepath_ad = filepath.replace('.jpg', '-ad.jpg')

            im = im.crop((left, top, right, bottom))
            im.save(filepath_ad)

            ads.append({
                'img_id': img_id,
                'orig': filepath,
                'ad': filepath_ad,
                'curr_url': curr_url,
                'twitter_account': self.find_twitter_account(curr_url),
                })

        self.driver.quit()

        with open(os.path.join(self.out_dir, run_id, 'out.json'), 'w') as f:
            f.write(json.dumps(ads, indent=2))

        return run_id, ads

    def upload_to_s3(self, run_id, ads):
        s3 = boto3.resource('s3')

        for ad in ads:
            filepath = ad['orig']
            filename = run_id + '-' + filepath.split('/')[-1]
            data = open(filepath, 'rb')
            s3.Bucket(conf.S3_BUCKET).put_object(Key=filename, Body=data)

if __name__ == '__main__':
    out_dir = 'out'
    if len(sys.argv) > 1:
        out_dir = sys.argv[1]

    c = Crawler(out_dir)
    run_id, ads = c.crawl()
    c.upload_to_s3(run_id, ads)
